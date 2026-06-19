"""DAG 引擎：StateGraph + 超步（super-step）执行器。

对应报告核心结论：
- 节点 = 函数（add_node），边 = add_edge，条件边 = add_conditional_edges（返回下一节点名）；
- 超步执行：每一步把当前 frontier 里的节点**并行**跑完，再算出下一批 frontier；
- 支持循环（条件边可指回上游，如测试失败回到 Coder）——故是「有向图」而非严格无环；
- 节点级重试 + interrupt 暂停 + checkpoint 恢复。

START / END 是两个保留节点名，分别表示入口与出口。
"""
from __future__ import annotations

import ast
import time
import traceback
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from .checkpoint import Checkpoint, Checkpointer
from .interrupt import Command, Interrupt, _MISSING
from .state import StateSchema

START = "__start__"
END = "__end__"

# 节点签名：接收 (state, ctx) → 返回 partial update（dict）或 None。
NodeFn = Callable[[Dict[str, Any], "NodeContext"], Optional[Dict[str, Any]]]
# 条件边路由函数：接收 state → 返回下一个（或多个）节点名。
RouterFn = Callable[[Dict[str, Any]], Any]

# 异常类型名 → 异常类的映射，用于 activity 缓存恢复时还原原始异常类型
_EXC_MAP: Dict[str, type] = {
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "RuntimeError": RuntimeError,
    "StopIteration": StopIteration,
    "ZeroDivisionError": ZeroDivisionError,
    "FileNotFoundError": FileNotFoundError,
    "PermissionError": PermissionError,
    "OSError": OSError,
    "ImportError": ImportError,
    "ModuleNotFoundError": ModuleNotFoundError,
    "LookupError": LookupError,
    "AssertionError": AssertionError,
    "OverflowError": OverflowError,
    "RecursionError": RecursionError,
    "EOFError": EOFError,
    "MemoryError": MemoryError,
}


def _get_source(fn: Any) -> str:
    """取 callable 的源码字符串。优先用 inspect.getsource（保留缩进与原貌），
    失败时退到去缩进的 repr。"""
    import inspect
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        import textwrap
        return textwrap.dedent(repr(fn))


@dataclass
class NodeContext:
    """传给节点的运行时上下文。"""

    node: str
    step: int
    attempt: int
    resume_value: Any = _MISSING  # 恢复时注入的人工值；正常执行为 _MISSING
    thread_id: str = ""
    _cp: Any = None  # Checkpointer 引用，供 activity() 使用

    def interrupt(self, payload: Any) -> Any:
        from .interrupt import interrupt as _interrupt
        return _interrupt(payload, self.resume_value)

    def tool(self, name: str, fn: Callable[[], Any],
            key: Optional[str] = None, **kwargs: Any) -> Any:
        """ctx.activity 的薄包装：所有工具调用走这里，自动获得缓存 + 审计。

        缓存键规则：
        - 自动加 "tool:" 前缀，避免与同节点 LLM activity 冲突
        - 若传 `key=<disambiguator>`，最终键是 `tool:<name>:<key>`
        - 若不传，键是 `tool:<name>`

        关于 cache 命中（同 key）的语义（CR 2026-06-17 1.3）：
        - **同 (name, key) 组合** 命中同一条缓存，无论 fn 是否不同
        - 这是**有意**的：cache 假设"同 name 同 disambiguator 的调用产生同结果"
        - 典型用法：每个 task 一个独立 key（key=task_id），让 P1-3 真实 Coder
          写多文件时各自独立缓存
        - 反例：传 `key="x"` 给 read_file A、再传 `key="x"` 给 read_file B
          → 第二次会返回 A 的内容（撞缓存）
        - 解决方案：**用更精确的 disambiguator**（如 file path、task id）

        kwargs 行为（CR 2026-06-17 2.4）：
        - 只识别 `input_summary`，透传给 activity()
        - 未知 kwargs 会被静默忽略并打 WARN（避免 typo 静默失败）
        """
        input_summary = kwargs.pop("input_summary", "") if kwargs else ""
        if kwargs:
            # CR 2026-06-17 2.4: 未知 kwargs 警告（可能是 typo）
            print(f"[ctx.tool] WARN: 忽略未知 kwargs: {list(kwargs.keys())}")
        full_key = f"tool:{name}:{key}" if key else f"tool:{name}"
        return self.activity(full_key, fn, input_summary=input_summary)

    def activity(self, key: str, fn: Callable[[], Any],
                 input_summary: str = "") -> Any:
        """以 (thread_id, node, step, key) 为键缓存 fn() 的结果。

        首次执行时调用 fn() 并写入 checkpointer.activity_results 表；
        后续调用（包括中断恢复）直接读取缓存，不再执行 fn()。
        fn() 抛异常时也写入 status="exception"，重入时重抛（保留异常类型）。

        每次缓存未命中时会自动记录 tool_call 日志（含执行耗时）。
        参数 input_summary 用于描述本次调用的输入概况。
        """
        if self._cp is None:
            return fn()
        cached = self._cp.get_activity(self.thread_id, self.node, self.step, key)
        if cached is not None:
            result, status = cached
            if status == "exception":
                # 尝试还原原始异常类型
                if isinstance(result, dict) and "type" in result and "message" in result:
                    exc_type = _EXC_MAP.get(result["type"], RuntimeError)
                    raise exc_type(result["message"])
                raise RuntimeError(str(result))
            return result
        t0 = time.time()
        try:
            result = fn()
        except Exception as exc:
            duration_ms = (time.time() - t0) * 1000
            exc_info = {"type": type(exc).__name__, "message": str(exc)}
            self._cp.put_activity(self.thread_id, self.node, self.step,
                                  key, exc_info, "exception")
            self._cp.log_tool_call(
                self.thread_id, self.node, self.step,
                tool_name=key, activity_key=key,
                input_summary=input_summary,
                output_summary=f"exception: {type(exc).__name__}",
                duration_ms=duration_ms, status="exception",
            )
            raise
        duration_ms = (time.time() - t0) * 1000
        output_summary = self._make_output_summary(result)
        self._cp.put_activity(self.thread_id, self.node, self.step,
                              key, result, "success")
        self._cp.log_tool_call(
            self.thread_id, self.node, self.step,
            tool_name=key, activity_key=key,
            input_summary=input_summary,
            output_summary=output_summary,
            duration_ms=duration_ms, status="success",
        )
        return result

    @staticmethod
    def _make_output_summary(result: Any) -> str:
        """从 result 自动生成 output_summary。"""
        if isinstance(result, str):
            return result[:100]
        if isinstance(result, bytes):
            return f"bytes(len={len(result)})"
        if isinstance(result, dict):
            return f"dict(keys={list(result.keys())})"
        if isinstance(result, list):
            return f"list(len={len(result)})"
        if isinstance(result, tuple):
            return f"tuple(len={len(result)})"
        if result is None:
            return "None"
        return str(result)[:100]


@dataclass
class _Node:
    name: str
    fn: NodeFn
    retries: int = 0           # 失败重试次数（不含首次）
    retry_backoff: float = 0.0  # 重试间隔秒


@dataclass
class RunResult:
    thread_id: str
    status: str                # completed | interrupted | failed
    state: Dict[str, Any]
    step: int
    interrupt_payload: Any = None
    error: Optional[str] = None


@dataclass
class ValidationIssue:
    """validate() 的一项发现。level 决定严重程度：
    - error：必须修复，编译时可直接 raise
    - warning：可疑但合法，需要人工确认
    - info：仅提示（如检测到循环）
    """
    level: str                 # "error" | "warning" | "info"
    message: str
    node: Optional[str] = None

    def __str__(self) -> str:
        prefix = {"error": "❌", "warning": "⚠️ ", "info": "ℹ️ "}.get(self.level, "")
        where = f" [{self.node}]" if self.node else ""
        return f"{prefix} {self.level.upper()}{where}: {self.message}"


class StateGraph:
    """图定义 + 编译。API 刻意贴近 LangGraph 的 add_node / add_edge。"""

    def __init__(self, schema: Optional[StateSchema] = None, max_steps: int = 50):
        self.schema = schema or StateSchema()
        self.max_steps = max_steps
        self._nodes: Dict[str, _Node] = {}
        self._edges: Dict[str, List[str]] = {}           # 静态边：node -> [下游节点]
        self._cond: Dict[str, RouterFn] = {}             # 条件边：node -> 路由函数
        self._entry: List[str] = []

    def add_node(self, name: str, fn: NodeFn, *, retries: int = 0,
                 retry_backoff: float = 0.0) -> "StateGraph":
        if name in (START, END):
            raise ValueError(f"{name} 为保留节点名")
        if name in self._nodes:
            raise ValueError(f"节点 {name} 已存在")
        self._nodes[name] = _Node(name, fn, retries, retry_backoff)
        return self

    def add_edge(self, src: str, dst: str) -> "StateGraph":
        """静态边。src=START 表示入口节点。"""
        if src == START:
            self._entry.append(dst)
        else:
            self._edges.setdefault(src, []).append(dst)
        return self

    def add_conditional_edges(self, src: str, router: RouterFn) -> "StateGraph":
        """条件边：router(state) 返回下一个节点名，或节点名列表（扇出），或 END。"""
        self._cond[src] = router
        return self

    def compile(self, checkpointer: Optional[Checkpointer] = None) -> "CompiledGraph":
        # 基本校验：引用的节点都存在
        referenced = set(self._entry)
        for outs in self._edges.values():
            referenced.update(outs)
        for n in referenced:
            if n != END and n not in self._nodes:
                raise ValueError(f"边引用了未定义的节点：{n}")
        if not self._entry:
            raise ValueError("缺少入口：请 add_edge(START, <node>)")
        return CompiledGraph(self, checkpointer or Checkpointer())

    # —— 拓扑静态分析：编译前发现非法结构 —— #
    def validate(self) -> List[ValidationIssue]:
        """对图定义做静态校验，编译前调用。

        返回所有发现的问题（error / warning / info），调用方可按需 raise。
        设计原则：能静态判定的尽量静态判定；条件边返回值无法静态推断时，
        给出 warning 让人工确认而不是直接报错。
        """
        issues: List[ValidationIssue] = []

        # 1) 入口必须存在
        if not self._entry:
            issues.append(ValidationIssue("error", "缺少入口：未连接 START"))
        else:
            for n in self._entry:
                if n not in self._nodes:
                    issues.append(ValidationIssue(
                        "error", f"入口引用了未定义节点: {n}"))

        # 2) 条件边函数必须可调用
        for src, router in self._cond.items():
            if not callable(router):
                issues.append(ValidationIssue(
                    "error", f"条件边函数不可调用: {repr(router)}", node=src))

        # 3) 静态边不能引用未定义节点
        for src, outs in self._edges.items():
            for dst in outs:
                if dst != END and dst not in self._nodes:
                    issues.append(ValidationIssue(
                        "error", f"边引用了未定义节点: {src} -> {dst}", node=src))

        # 4) BFS 可达性：所有节点必须能从入口走到
        # 条件边目标未知：尽量用 AST 提取的字符串字面量作为可能目标集，
        # 静态分析拿不到时（lambda/partial 等）才退到「所有节点」这种最保守情况
        reachable: Set[str] = set()
        queue: deque = deque(self._entry)
        while queue:
            n = queue.popleft()
            if n in reachable or n == END or n not in self._nodes:
                continue
            reachable.add(n)
            for out in self._edges.get(n, []):
                if out not in reachable and out != END and out in self._nodes:
                    queue.append(out)
            if n in self._cond:
                targets = self._static_string_returns(self._cond[n])
                if not targets:
                    targets = set(self._nodes.keys())
                for m in targets:
                    if m not in reachable and m != END and m in self._nodes:
                        queue.append(m)
        for n in self._nodes:
            if n not in reachable:
                issues.append(ValidationIssue(
                    "error", f"节点不可达（从 START 出发走不到）", node=n))

        # 5) 是否有路径到 END：反向 BFS
        reaches_end: Set[str] = set()
        queue = deque()
        for n in self._nodes:
            outs = self._edges.get(n, [])
            # 静态边连到 END、没有任何出边、或者有条件边，都算「可能到 END」
            if END in outs or not outs or n in self._cond:
                queue.append(n)
                reaches_end.add(n)
        # 反向传播：任何节点能走到 reaches_end 中的节点，自己也算
        incoming: Dict[str, List[str]] = {}
        for src, outs in self._edges.items():
            for o in outs:
                incoming.setdefault(o, []).append(src)
        while queue:
            n = queue.popleft()
            for src in incoming.get(n, []):
                if src not in reaches_end:
                    reaches_end.add(src)
                    queue.append(src)
        for n in self._nodes:
            if n not in reaches_end:
                issues.append(ValidationIssue(
                    "warning", f"节点没有路径到 END（疑似死胡同）", node=n))

        # 6) 重复边
        for src, outs in self._edges.items():
            counts: Dict[str, int] = {}
            for o in outs:
                counts[o] = counts.get(o, 0) + 1
            for o, c in counts.items():
                if c > 1:
                    issues.append(ValidationIssue(
                        "warning", f"重复边 {src} -> {o}（出现 {c} 次）", node=src))

        # 7) 条件边返回值静态分析：从源码 AST 中提取字符串字面量
        for src, router in self._cond.items():
            targets = self._static_string_returns(router)
            for lit in targets:
                if lit == END:
                    continue
                if lit not in self._nodes:
                    issues.append(ValidationIssue(
                        "warning", f"条件边可能返回未定义节点 {repr(lit)}", node=src))

        # 8) 循环检测：仅看静态边，不阻止编译（循环是合法功能）
        if self._has_cycle():
            issues.append(ValidationIssue(
                "info", "检测到静态边循环（合法，已被 max_steps 限制）"))

        return issues

    def _static_string_returns(self, fn: RouterFn) -> Set[str]:
        """从路由函数源码中提取所有字符串字面量 return 值（启发式）。

        处理直接 return、if/else 三元、and/or 短路、列表/元组等组合形式；
        仅看 AST 字面量，不模拟实际控制流，所以会有少量误报（warning 级别
        本来就允许噪声）。
        """
        literals: Set[str] = set()
        try:
            src = _get_source(fn)
        except (OSError, TypeError):
            return literals
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return literals

        def walk(expr: ast.AST) -> None:
            if expr is None:
                return
            if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
                literals.add(expr.value)
                return
            if isinstance(expr, ast.Str):
                literals.add(expr.s)
                return
            if isinstance(expr, (ast.IfExp, ast.BoolOp)):
                # 三元 / and-or：所有分支都可能是返回值
                if isinstance(expr, ast.IfExp):
                    walk(expr.body)
                    walk(expr.orelse)
                else:
                    for v in expr.values:
                        walk(v)
                return
            if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
                for elt in expr.elts:
                    walk(elt)
                return
            if isinstance(expr, ast.Dict):
                for v in expr.values:
                    walk(v)
                return
            if isinstance(expr, ast.Call):
                # 函数调用的返回值无法静态分析，保守忽略
                return
            # 其它节点（Name、Attribute 等）— 不递归到子节点，宁缺勿滥

        # 手动遍历而非 ast.walk：先定位被分析的 root function（route_ghost 等
        # 模块级函数 def，对应 inspect.getsource 返回的源码的顶层 FunctionDef），
        # 再遍历其 body，遇到嵌套函数/lambda 直接跳过其子树，避免把
        # `def helper(): return "ghost"` 这种辅助函数的 return 误提取为
        # 路由函数的可能目标。
        _SKIP_SUBTREE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        root = None
        if isinstance(tree, ast.Module) and tree.body:
            first = tree.body[0]
            if isinstance(first, _SKIP_SUBTREE):
                root = first

        if root is None:
            # fallback：源码不是单函数（如 eval 字符串 / 模块顶层 return）
            # 退到 ast.walk，不做嵌套过滤，宁可误报也不漏报
            for node in ast.walk(tree):
                if isinstance(node, ast.Return) and node.value is not None:
                    walk(node.value)
        else:
            def visit(n: ast.AST) -> None:
                for child in ast.iter_child_nodes(n):
                    if isinstance(child, _SKIP_SUBTREE):
                        continue  # 嵌套函数/lambda：跳子树，不递归
                    if isinstance(child, ast.Return) and child.value is not None:
                        walk(child.value)
                    visit(child)
            visit(root)
        return literals

    def _has_cycle(self) -> bool:
        """DFS 检测静态边中的环。条件边不参与（无法静态判定）。"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self._nodes}

        def dfs(n: str) -> bool:
            color[n] = GRAY
            for out in self._edges.get(n, []):
                if out == END or out not in self._nodes:
                    continue
                if color[out] == GRAY:
                    return True
                if color[out] == WHITE and dfs(out):
                    return True
            color[n] = BLACK
            return False

        return any(color[n] == WHITE and dfs(n) for n in self._nodes)

    def to_mermaid(self) -> str:
        """把图导出为 Mermaid 语法。条件边用虚线 + router 函数名标注。

        节点名做基础转义（替换 Mermaid 保留字符），label 用双引号包裹。
        """
        lines = ["graph TD"]

        def safe(name: str) -> str:
            # Mermaid 节点 ID 不能含空格或保留符号
            return name.replace(" ", "_").replace("-", "_").replace(".", "_")

        # 节点定义（含 START/END）
        lines.append(f"    {safe(START)}([\"{START}\"]):::startNode")
        for n in sorted(self._nodes.keys()):
            label = n
            lines.append(f"    {safe(n)}[\"{label}\"]")
        lines.append(f"    {safe(END)}([\"{END}\"]):::endNode")

        # 静态边
        for src, outs in self._edges.items():
            for dst in outs:
                lines.append(f"    {safe(src)} --> {safe(dst)}")

        # 入口边（add_edge(START, ...) 不在 self._edges 里）
        for dst in self._entry:
            lines.append(f"    {safe(START)} --> {safe(dst)}")

        # 条件边：虚线 + 函数名标注，可能目标集作为标签提示
        for src, fn in self._cond.items():
            fn_name = getattr(fn, "__name__", "cond")
            hints = sorted(self._static_string_returns(fn) - {END})
            if hints:
                label = f"{fn_name} → {{{','.join(hints)}}}"
            else:
                label = fn_name
            lines.append(f"    {safe(src)} -.->|{label}| (unknown)")

        return "\n".join(lines) + "\n"

    # —— 内部：给定刚跑完的节点，算出它的下游 —— #
    def _successors(self, node: str, state: Dict[str, Any]) -> List[str]:
        if node in self._cond:
            out = self._cond[node](state)
            outs = out if isinstance(out, list) else [out]
            return [o for o in outs if o and o != END]
        return [o for o in self._edges.get(node, []) if o != END]

    def _reaches_end(self, node: str, state: Dict[str, Any]) -> bool:
        """节点是否显式指向 END（条件边返回 END 或静态边连到 END）。"""
        if node in self._cond:
            out = self._cond[node](state)
            outs = out if isinstance(out, list) else [out]
            return END in outs or len(self._successors(node, state)) == 0
        edges = self._edges.get(node, [])
        if not edges:
            return True  # 无出边 = 汇点，视作到达 END
        return END in edges


class CompiledGraph:
    """可执行图：负责 super-step 调度、并行、checkpoint、中断与恢复。"""

    def __init__(self, g: StateGraph, checkpointer: Checkpointer):
        self.g = g
        self.cp = checkpointer
        self._pool = ThreadPoolExecutor(max_workers=8)

    def invoke(self, initial_state: Dict[str, Any], thread_id: Optional[str] = None,
               command: Optional[Command] = None) -> RunResult:
        """运行或恢复一个 thread。

        - 全新运行：传 initial_state（thread_id 可省略，自动生成）。
        - 恢复运行：传相同 thread_id + Command(resume=...)，从最近 checkpoint 续跑，
          已完成节点不重跑（state 直接来自 checkpoint）。
        """
        thread_id = thread_id or str(uuid.uuid4())
        prev = self.cp.latest(thread_id)

        if prev and prev.status in ("interrupted", "failed"):
            # —— 恢复路径 —— state 与 frontier 都来自 checkpoint，绝不回放已完成节点
            state = prev.state
            frontier = list(prev.frontier)
            step = prev.step
            resume_for = {n: (command.resume if command else None)
                          for n in frontier} if prev.status == "interrupted" else {}
            self.cp.log_event(thread_id, "resume",
                              {"from_step": step, "frontier": frontier})
        else:
            state = self.g.schema.merge({}, initial_state)
            frontier = list(self.g._entry)
            step = 0
            resume_for = {}
            self.cp.log_event(thread_id, "start", {"entry": frontier})
            self.cp.put(Checkpoint(thread_id, step, state, frontier, "running"))

        return self._run_loop(thread_id, state, frontier, step, resume_for)

    # —— 主循环：每轮跑完一个 super-step —— #
    def _run_loop(self, thread_id: str, state: Dict[str, Any], frontier: List[str],
                  step: int, resume_for: Dict[str, Any]) -> RunResult:
        while frontier:
            if step >= self.g.max_steps:
                self.cp.put(Checkpoint(thread_id, step, state, frontier, "failed"))
                return RunResult(thread_id, "failed", state, step,
                                 error=f"超过 max_steps={self.g.max_steps}（可能存在死循环）")
            step += 1
            # 去重：同一 super-step 内一个节点只跑一次
            batch = list(dict.fromkeys(frontier))
            self.cp.log_event(thread_id, "superstep", {"step": step, "nodes": batch})

            # 1) 并行执行本批节点，收集各自的 partial update
            futures = {
                self._pool.submit(self._exec_node, thread_id, self.g._nodes[n],
                                  state, step, resume_for.get(n, _MISSING)): n
                for n in batch
            }
            updates: Dict[str, Dict[str, Any]] = {}
            for fut in futures:
                node = futures[fut]
                outcome = fut.result()
                if outcome["kind"] == "interrupt":
                    # 任一节点请求人工介入：存盘暂停，frontier 保留未完成的整批
                    self.cp.put(Checkpoint(thread_id, step - 1, state, batch,
                                           "interrupted", outcome["payload"]))
                    self.cp.log_event(thread_id, "interrupt",
                                      {"node": node, "payload": outcome["payload"]})
                    return RunResult(thread_id, "interrupted", state, step - 1,
                                     interrupt_payload=outcome["payload"])
                if outcome["kind"] == "error":
                    self.cp.put(Checkpoint(thread_id, step - 1, state, batch, "failed"))
                    self.cp.log_event(thread_id, "error",
                                      {"node": node, "error": outcome["error"]})
                    return RunResult(thread_id, "failed", state, step - 1,
                                     error=f"{node}: {outcome['error']}")
                updates[node] = outcome["update"] or {}
            resume_for = {}  # resume 值只对恢复后的第一个 super-step 有效

            # 2) 合并所有 update（顺序按 batch，确保确定性）
            for n in batch:
                state = self.g.schema.merge(state, updates[n])

            # 3) 计算下一批 frontier
            next_frontier: List[str] = []
            for n in batch:
                next_frontier.extend(self.g._successors(n, state))
            frontier = list(dict.fromkeys(next_frontier))

            # 4) 落盘本 super-step 的 checkpoint
            status = "running" if frontier else "completed"
            self.cp.put(Checkpoint(thread_id, step, state, frontier, status))

        self.cp.log_event(thread_id, "complete", {"step": step})
        return RunResult(thread_id, "completed", state, step)

    # —— 单节点执行：含重试与 interrupt 捕获 —— #
    def _exec_node(self, thread_id: str, node: _Node, state: Dict[str, Any],
                   step: int, resume_value: Any) -> Dict[str, Any]:
        attempt = 0
        while True:
            ctx = NodeContext(node.name, step, attempt, resume_value,
                                thread_id, self.cp)
            try:
                update = node.fn(state, ctx)
                self.cp.log_event(thread_id, "node_ok",
                                  {"node": node.name, "attempt": attempt})
                return {"kind": "ok", "update": update}
            except Interrupt as it:
                return {"kind": "interrupt", "payload": it.payload}
            except Exception as exc:  # noqa: BLE001 — 引擎需兜住任意节点异常
                if attempt < node.retries:
                    self.cp.log_event(thread_id, "node_retry",
                                      {"node": node.name, "attempt": attempt,
                                       "error": str(exc)})
                    attempt += 1
                    resume_value = _MISSING  # 重试不复用 resume
                    if node.retry_backoff:
                        time.sleep(node.retry_backoff)
                    continue
                return {"kind": "error",
                        "error": f"{exc}\n{traceback.format_exc(limit=2)}"}
