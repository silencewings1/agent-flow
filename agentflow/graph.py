"""DAG 引擎：StateGraph + 超步（super-step）执行器。

对应报告核心结论：
- 节点 = 函数（add_node），边 = add_edge，条件边 = add_conditional_edges（可返回节点名或 Send）；
- 超步执行：每一步把当前 frontier 里的节点**并行**跑完，再算出下一批 frontier；
- 支持循环（条件边可指回上游，如测试失败回到 Coder）——故是「有向图」而非严格无环；
- 节点级重试 + interrupt 暂停 + checkpoint 恢复。

START / END 是两个保留节点名，分别表示入口与出口。
"""

import ast
import copy
import hashlib
import json
import time
import traceback
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Set

from .checkpoint import Checkpoint, Checkpointer
from .interrupt import Command, Interrupt, _MISSING
from .state import StateSchema

START = "__start__"
END = "__end__"

# 节点签名：接收 (state, ctx) → 返回 partial update（dict）或 None。
NodeFn = Callable[[dict[str, Any], "NodeContext"], dict[str, Any | None]]
# 条件边路由函数：接收 state → 返回下一个（或多个）节点名。
RouterFn = Callable[[dict[str, Any]], Any]


@dataclass
class Send:
    """动态调度一个 worker 节点，并给该实例注入独立 state slice。"""

    node: str
    arg: dict[str, Any] = field(default_factory=dict)
    key: str | None = None

# 异常类型名 → 异常类的映射，用于 activity 缓存恢复时还原原始异常类型
_EXC_MAP: dict[str, type] = {
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
    instance_id: str = ""

    def interrupt(self, payload: Any) -> Any:
        from .interrupt import interrupt as _interrupt
        return _interrupt(payload, self.resume_value)

    def tool(self, name: str, fn: Callable[[], Any],
            key: str | None = None, **kwargs: Any) -> Any:
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
            print(f"[ctx.tool] WARN: 忽略未知 kwargs: {list(kwargs.keys())=}")
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
        activity_key = self._activity_key(key)
        cached = self._cp.get_activity(self.thread_id, self.node, self.step, activity_key)
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
                                  activity_key, exc_info, "exception")
            self._cp.log_tool_call(
                self.thread_id, self.node, self.step,
                tool_name=key, activity_key=activity_key,
                input_summary=input_summary,
                output_summary=f"exception: {type(exc).__name__}",
                duration_ms=duration_ms, status="exception",
            )
            raise
        duration_ms = (time.time() - t0) * 1000
        output_summary = self._make_output_summary(result)
        self._cp.put_activity(self.thread_id, self.node, self.step,
                              activity_key, result, "success")
        self._cp.log_tool_call(
            self.thread_id, self.node, self.step,
            tool_name=key, activity_key=activity_key,
            input_summary=input_summary,
            output_summary=output_summary,
            duration_ms=duration_ms, status="success",
        )
        return result

    def _activity_key(self, key: str) -> str:
        if not self.instance_id or self.instance_id == self.node:
            return key
        return f"{self.instance_id}:{key}"

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
class _SubgraphSpec:
    """子图节点配置：把一个已编译的 CompiledGraph 注册为父图的一个节点。

    input_map: {parent_state_key: child_state_key} —— 父 → 子 字段映射
    output_map: {child_state_key: parent_state_key} —— 子 → 父 字段映射
    """

    name: str
    subgraph: "CompiledGraph"
    input_map: dict[str, str]
    output_map: dict[str, str]


def _make_subgraph_fn(spec: "_SubgraphSpec") -> NodeFn:
    """把子图 CompiledGraph 包成一个 NodeFn，供父图当普通节点调度。

    wrapper 语义：
    - 用 input_map 从父 state 构造子图初始 state
    - 用稳定子 thread_id 跑 sub.invoke；resume 时透传 Command
    - 子图 interrupted → raise Interrupt（父图 _exec_node 现有 except 捕获）
    - 子图 failed → raise RuntimeError（走父图节点级重试）
    - 子图 completed → output_map 映射回父 partial update
    """
    sub = spec.subgraph

    def subgraph_node(state: dict[str, Any], ctx: "NodeContext") -> dict[str, Any | None]:
        # 1) input_map：父 state → 子 state（仅映射声明的字段）
        child_state: dict[str, Any] = {}
        for parent_key, child_key in spec.input_map.items():
            if parent_key in state:
                child_state[child_key] = state[parent_key]
        # 2) 稳定子 thread_id：含父 step + instance_id，回环 / Send 场景各自独立
        sub_tid = f"{ctx.thread_id}::sub::{spec.name}::s{ctx.step}::{ctx.instance_id}"
        # 3) resume 透传：父图 resume_for → ctx.resume_value → Command
        command = None
        if ctx.resume_value is not _MISSING:
            command = Command(resume=ctx.resume_value)
        result = sub.invoke(child_state, thread_id=sub_tid, command=command)
        # 4) 子图结果 → 父图 partial update / 异常
        if result.status == "interrupted":
            raise Interrupt(result.interrupt_payload)
        if result.status == "failed":
            raise RuntimeError(f"子图 {spec.name} 失败: {result.error}")
        # 5) output_map：子 state → 父 partial update（仅映射存在的字段）
        update: dict[str, Any] = {}
        for child_key, parent_key in spec.output_map.items():
            if child_key in result.state:
                update[parent_key] = result.state[child_key]
        return update

    subgraph_node.__name__ = f"subgraph:{spec.name}"
    return subgraph_node


@dataclass
class _Barrier:
    sources: tuple[str, ...]
    target: str


@dataclass
class RunResult:
    thread_id: str
    status: str                # completed | interrupted | failed
    state: dict[str, Any]
    step: int
    interrupt_payload: Any = None
    error: str | None = None


@dataclass
class ValidationIssue:
    """validate() 的一项发现。level 决定严重程度：
    - error：必须修复，编译时可直接 raise
    - warning：可疑但合法，需要人工确认
    - info：仅提示（如检测到循环）
    """
    level: str                 # "error" | "warning" | "info"
    message: str
    node: str | None = None

    def __str__(self) -> str:
        prefix = {"error": "❌", "warning": "⚠️ ", "info": "ℹ️ "}.get(self.level, "")
        where = f" [{self.node}]" if self.node else ""
        return f"{prefix} {self.level.upper()}{where}: {self.message}"


class StateGraph:
    """图定义 + 编译。API 刻意贴近 LangGraph 的 add_node / add_edge。"""

    def __init__(self, schema: StateSchema | None = None, max_steps: int = 50):
        self.schema = schema or StateSchema()
        self.max_steps = max_steps
        self._nodes: dict[str, _Node] = {}
        self._edges: dict[str, list[str]] = {}           # 静态边：node -> [下游节点]
        self._barriers: list[_Barrier] = []              # 多源 barrier：sources -> target
        self._cond: dict[str, RouterFn] = {}             # 条件边：node -> 路由函数
        self._entry: list[str] = []
        self._subgraphs: dict[str, _SubgraphSpec] = {}   # 子图节点：name -> spec

    def add_node(self, name: str, fn: NodeFn, *, retries: int = 0,
                 retry_backoff: float = 0.0) -> "StateGraph":
        if name in (START, END):
            raise ValueError(f"{name} 为保留节点名")
        if name in self._nodes:
            raise ValueError(f"节点 {name} 已存在")
        self._nodes[name] = _Node(name, fn, retries, retry_backoff)
        return self

    def add_subgraph(self, name: str, subgraph: "CompiledGraph",
                     input_map: dict[str, str | None] = None,
                     output_map: dict[str, str | None] = None,
                     retries: int = 0, retry_backoff: float = 0.0) -> "StateGraph":
        """把一个已编译的 CompiledGraph 注册为父图的一个节点。

        子图节点对外与普通节点无异（可被 add_edge / add_conditional_edges 引用），
        运行时由 _make_subgraph_fn 生成的 wrapper 驱动：

        - input_map {parent_key: child_key}：从父 state 提取字段注入子图初始 state
        - 子图独立运行（自己的 super-step 循环、thread_id、max_steps）
        - 子图 status=interrupted → raise Interrupt(payload) 冒泡到父图
        - 子图 status=failed → raise RuntimeError 冒泡（走父图节点级重试）
        - 子图 status=completed → output_map {child_key: parent_key} 写回父 state

        子图共享父 checkpointer，thread_id 为
        ``{parent_tid}::sub::{name}::s{parent_step}::{instance_id}``，
        含父 step + instance_id 保证回环 / Send 场景下多次重入各自独立且 thread_id 稳定，
        resume 时子图用自己的 checkpoint 续跑（子图内已完成节点不重跑）。

        子图 max_steps 由子图自己的 StateGraph 决定，与父图独立。

        **retries/retry_backoff**：子图节点级重试（与 add_node 语义一致）。
        注意：子图因 max_steps 死循环 failed 后，重试时子图从 failed checkpoint
        恢复（sub_tid 不变），简单重试不会解决问题。retries 更适合子图内瞬时错误
        （如 LLM 超时）的场景。
        """
        if not isinstance(subgraph, CompiledGraph):
            raise TypeError(
                f"add_subgraph 的 subgraph 必须是 CompiledGraph，得到 {type(subgraph).__name__}"
            )
        if name in (START, END):
            raise ValueError(f"{name} 为保留节点名")
        if name in self._nodes:
            raise ValueError(f"节点 {name} 已存在")
        spec = _SubgraphSpec(
            name=name,
            subgraph=subgraph,
            input_map=dict(input_map or {}),
            output_map=dict(output_map or {}),
        )
        self._subgraphs[name] = spec
        self._nodes[name] = _Node(name, _make_subgraph_fn(spec),
                                  retries=retries, retry_backoff=retry_backoff)
        return self

    def add_edge(self, src: Any, dst: str) -> "StateGraph":
        """静态边。src=START 表示入口节点。"""
        if isinstance(src, (list, tuple)):
            sources = tuple(str(s) for s in src)
            if not sources:
                raise ValueError("barrier 边至少需要一个源节点")
            self._barriers.append(_Barrier(sources, dst))
            return self
        if src == START:
            self._entry.append(dst)
        else:
            self._edges.setdefault(src, []).append(dst)
        return self

    def add_conditional_edges(self, src: str, router: RouterFn) -> "StateGraph":
        """条件边：router(state) 返回节点名、Send、列表，或 END。"""
        self._cond[src] = router
        return self

    def compile(self, checkpointer: Checkpointer | None = None) -> "CompiledGraph":
        # 基本校验：引用的节点都存在
        referenced = set(self._entry)
        referenced.update(self._edges.keys())
        for outs in self._edges.values():
            referenced.update(outs)
        referenced.update(self._cond.keys())
        for barrier in self._barriers:
            referenced.update(barrier.sources)
            referenced.add(barrier.target)
        for n in referenced:
            if n != END and n not in self._nodes:
                raise ValueError(f"边引用了未定义的节点：{n}")
        if not self._entry:
            raise ValueError("缺少入口：请 add_edge(START, <node>)")
        return CompiledGraph(self, checkpointer or Checkpointer())

    # —— 拓扑静态分析：编译前发现非法结构 —— #
    def validate(self) -> list[ValidationIssue]:
        """对图定义做静态校验，编译前调用。

        返回所有发现的问题（error / warning / info），调用方可按需 raise。
        设计原则：能静态判定的尽量静态判定；条件边返回值无法静态推断时，
        给出 warning 让人工确认而不是直接报错。
        """
        issues: list[ValidationIssue] = []

        # 1) 入口必须存在
        if not self._entry:
            issues.append(ValidationIssue("error", "缺少入口：未连接 START"))
        else:
            for n in self._entry:
                if n not in self._nodes:
                    issues.append(ValidationIssue(
                        "error", f"入口引用了未定义节点: {n}"))

        # 2) 条件边：源节点必须已定义，router 必须可调用
        for src, router in self._cond.items():
            if src != END and src not in self._nodes:
                issues.append(ValidationIssue(
                    "error", f"条件边引用了未定义节点: {src}", node=src))
            if not callable(router):
                issues.append(ValidationIssue(
                    "error", f"条件边函数不可调用: {repr(router)}", node=src))

        # 3) 静态边不能引用未定义节点
        for src, outs in self._edges.items():
            if src not in self._nodes:
                issues.append(ValidationIssue(
                    "error", f"边引用了未定义节点: {src}", node=src))
            for dst in outs:
                if dst != END and dst not in self._nodes:
                    issues.append(ValidationIssue(
                        "error", f"边引用了未定义节点: {src} -> {dst}", node=src))

        for barrier in self._barriers:
            for src in barrier.sources:
                if src not in self._nodes:
                    issues.append(ValidationIssue(
                        "error", f"barrier 边引用了未定义源节点: {src}", node=src))
            if barrier.target != END and barrier.target not in self._nodes:
                issues.append(ValidationIssue(
                    "error",
                    f"barrier 边引用了未定义目标节点: {barrier.target}",
                    node=barrier.target,
                ))

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
            for out in self._barrier_targets_from(n):
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
            barrier_outs = self._barrier_targets_from(n)
            # 静态边连到 END、没有任何出边、或者有条件边，都算「可能到 END」
            if END in outs or END in barrier_outs or (not outs and not barrier_outs) or n in self._cond:
                queue.append(n)
                reaches_end.add(n)
        # 反向传播：任何节点能走到 reaches_end 中的节点，自己也算
        incoming: dict[str, list[str]] = {}
        for src, outs in self._edges.items():
            for o in outs:
                incoming.setdefault(o, []).append(src)
        for barrier in self._barriers:
            for src in barrier.sources:
                incoming.setdefault(barrier.target, []).append(src)
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
            counts: dict[str, int] = {}
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
            ast_str = getattr(ast, "Str", None)
            if ast_str is not None and isinstance(expr, ast_str):
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

    def _barrier_targets_from(self, node: str) -> list[str]:
        return [b.target for b in self._barriers if node in b.sources]

    def _has_cycle(self) -> bool:
        """DFS 检测静态边中的环。条件边不参与（无法静态判定）。"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self._nodes}

        def dfs(n: str) -> bool:
            color[n] = GRAY
            for out in self._edges.get(n, []) + self._barrier_targets_from(n):
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
            if n in self._subgraphs:
                lines.append(f"    {safe(n)}[[\"子图: {n}\"]]")
            else:
                lines.append(f"    {safe(n)}[\"{n}\"]")
        lines.append(f"    {safe(END)}([\"{END}\"]):::endNode")

        # 静态边
        for src, outs in self._edges.items():
            for dst in outs:
                lines.append(f"    {safe(src)} --> {safe(dst)}")
        for barrier in self._barriers:
            label = "barrier"
            for src in barrier.sources:
                lines.append(f"    {safe(src)} -->|{label}| {safe(barrier.target)}")

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
    def _successors(self, node: str, state: dict[str, Any]) -> list[str]:
        if node in self._cond:
            outs = out if isinstance(out := self._cond[node](state), (list, tuple)) else [out]
            names = []
            for o in outs:
                if isinstance(o, Send):
                    names.append(o.node)
                elif o and o != END:
                    names.append(o)
            return names
        return [o for o in self._edges.get(node, []) + self._barrier_targets_from(node)
                if o != END]

    def _reaches_end(self, node: str, state: dict[str, Any]) -> bool:
        """节点是否显式指向 END（条件边返回 END 或静态边连到 END）。"""
        if node in self._cond:
            out = self._cond[node](state)
            outs = out if isinstance(out, list) else [out]
            return END in outs or len(self._successors(node, state)) == 0
        edges = self._edges.get(node, [])
        barrier_edges = self._barrier_targets_from(node)
        if not edges and not barrier_edges:
            return True  # 无出边 = 汇点，视作到达 END
        return END in edges or END in barrier_edges


class CompiledGraph:
    """可执行图：负责 super-step 调度、并行、checkpoint、中断与恢复。"""

    def __init__(self, g: StateGraph, checkpointer: Checkpointer):
        self.g = g
        self.cp = checkpointer
        self._pool = ThreadPoolExecutor(max_workers=8)

    @staticmethod
    def _node_item(node: str, instance_id: str | None = None,
                   arg: dict[str, Any | None] = None) -> dict[str, Any]:
        return {
            "kind": "node",
            "node": node,
            "instance_id": instance_id or node,
            "arg": dict(arg or {}),
        }

    @staticmethod
    def _barrier_item(sources: tuple[str, ...], target: str,
                      ready: list[str | None] = None) -> dict[str, Any]:
        return {
            "kind": "barrier",
            "sources": list(sources),
            "target": target,
            "ready": list(ready or []),
        }

    def _normalize_frontier(self, frontier: list[Any]) -> list[dict[str, Any]]:
        items = []
        for item in frontier:
            match item:
                case str():
                    items.append(self._node_item(item))
                case dict(kind="barrier"):
                    items.append({
                        "kind": "barrier",
                        "sources": list(item.get("sources", [])),
                        "target": item.get("target"),
                        "ready": list(item.get("ready", [])),
                    })
                case dict():
                    node = item.get("node")
                    items.append(self._node_item(
                        node,
                        item.get("instance_id") or node,
                        item.get("arg") or {},
                    ))
        return items

    @staticmethod
    def _item_key(item: dict[str, Any]) -> str:
        match item.get("kind"):
            case "barrier":
                sources = ",".join(item.get("sources", []))
                ready = ",".join(sorted(item.get("ready", [])))
                return f"barrier:{sources}->{item.get('target')}:{ready}"
            case _:
                return f"node:{item.get('node')}:{item.get('instance_id')}"

    @staticmethod
    def _item_label(item: dict[str, Any]) -> str:
        match item.get("kind"):
            case "barrier":
                return "barrier:{}->{} ready={}".format(
                    ",".join(item.get("sources", [])),
                    item.get("target"),
                    item.get("ready", []),
                )
            case _:
                node = item.get("node")
                instance_id = item.get("instance_id")
                if instance_id and instance_id != node:
                    return f"{node}#{instance_id}"
                return str(node)

    def _dedupe_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: Set[str] = set()
        out = []
        for item in items:
            key = self._item_key(item)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    def _resolve_barriers(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nodes = []
        barriers: dict[tuple[tuple[str, ...], str], Set[str]] = {}
        for item in items:
            if item.get("kind") != "barrier":
                nodes.append(item)
                continue
            sources = tuple(item.get("sources", []))
            target = item.get("target")
            key = (sources, target)
            ready = barriers.setdefault(key, set())
            ready.update(item.get("ready", []))

        pending = []
        ready_nodes = []
        for (sources, target), ready in barriers.items():
            if set(sources).issubset(ready):
                if target and target != END:
                    ready_nodes.append(self._node_item(target))
            else:
                pending.append(self._barrier_item(sources, target, sorted(ready)))
        return pending + nodes + ready_nodes

    @staticmethod
    def _stable_hash(value: Any) -> str:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]

    def _send_to_item(self, source_item: dict[str, Any], send: Send,
                      step: int, index: int) -> dict[str, Any]:
        if send.node not in self.g._nodes:
            raise ValueError(f"Send 指向未定义节点: {send.node}")
        identity = send.key if send.key is not None else {
            "step": step,
            "index": index,
            "arg": send.arg,
        }
        raw = {
            "source": source_item.get("instance_id"),
            "target": send.node,
            "identity": identity,
        }
        instance_id = f"{send.node}:{self._stable_hash(raw)}"
        return self._node_item(send.node, instance_id, send.arg)

    def _successor_items(self, item: dict[str, Any], state: dict[str, Any],
                         step: int) -> list[dict[str, Any]]:
        node = item["node"]
        barrier_items = [
            self._barrier_item(barrier.sources, barrier.target, [node])
            for barrier in self.g._barriers
            if node in barrier.sources
        ]
        if node in self.g._cond:
            out = self.g._cond[node](state)
            outs = out if isinstance(out, (list, tuple)) else [out]
            items = []
            for index, value in enumerate(outs):
                if not value or value == END:
                    continue
                if isinstance(value, Send):
                    items.append(self._send_to_item(item, value, step, index))
                else:
                    target = str(value)
                    if target not in self.g._nodes:
                        raise ValueError(f"条件边返回未定义节点: {target}")
                    items.append(self._node_item(target))
            return items + barrier_items

        items = [self._node_item(o) for o in self.g._edges.get(node, []) if o != END]
        return items + barrier_items

    def invoke(self, initial_state: dict[str, Any], thread_id: str | None = None,
               command: Command | None = None) -> RunResult:
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
            frontier = self._normalize_frontier(list(prev.frontier))
            step = prev.step
            resume_for = {
                self._item_key(n): (command.resume if command else None)
                for n in frontier if n.get("kind") == "node"
            } if prev.status == "interrupted" else {}
            self.cp.log_event(thread_id, "resume",
                              {"from_step": step, "frontier": frontier})
        else:
            state = self.g.schema.merge({}, initial_state)
            frontier = [self._node_item(n) for n in self.g._entry if n != END]
            step = 0
            resume_for = {}
            self.cp.log_event(thread_id, "start", {"entry": frontier})
            self.cp.put(Checkpoint(thread_id, step, state, frontier, "running"))

        return self._run_loop(thread_id, state, frontier, step, resume_for)

    # —— 主循环：每轮跑完一个 super-step —— #
    def _run_loop(self, thread_id: str, state: dict[str, Any], frontier: list[Any],
                  step: int, resume_for: dict[str, Any]) -> RunResult:
        frontier = self._dedupe_items(self._resolve_barriers(
            self._normalize_frontier(frontier)
        ))
        while frontier:
            if step >= self.g.max_steps:
                self.cp.put(Checkpoint(thread_id, step, state, frontier, "failed"))
                return RunResult(thread_id, "failed", state, step,
                                 error=f"超过 max_steps={self.g.max_steps}（可能存在死循环）")
            waiting = [i for i in frontier if i.get("kind") == "barrier"]
            executable = [i for i in frontier if i.get("kind") == "node"]
            if not executable:
                self.cp.put(Checkpoint(thread_id, step, state, frontier, "failed"))
                return RunResult(
                    thread_id, "failed", state, step,
                    error="barrier 等待的源节点无法继续执行",
                )
            step += 1
            batch = self._dedupe_items(executable)
            current_frontier = waiting + batch
            self.cp.log_event(
                thread_id,
                "superstep",
                {"step": step, "nodes": [self._item_label(i) for i in batch]},
            )

            # 1) 并行执行本批节点，收集各自的 partial update
            futures = {}
            for item in batch:
                fut = self._pool.submit(
                    self._exec_node,
                    thread_id,
                    self.g._nodes[item["node"]],
                    item,
                    state,
                    step,
                    resume_for.get(self._item_key(item), _MISSING),
                )
                futures[fut] = item
            updates: dict[str, dict[str, Any]] = {}
            processed = set()
            interrupt_outcome = None
            interrupt_item = None
            error_outcome = None
            error_item = None

            def record_outcome(fut: Any) -> None:
                nonlocal interrupt_outcome, interrupt_item, error_outcome, error_item
                item = futures[fut]
                item_key = self._item_key(item)
                outcome = fut.result()
                processed.add(fut)
                match outcome["kind"]:
                    case "ok":
                        updates[item_key] = outcome["update"] or {}
                    case "interrupt" if interrupt_outcome is None and error_outcome is None:
                        interrupt_outcome = outcome
                        interrupt_item = item
                    case "error" if error_outcome is None and interrupt_outcome is None:
                        error_outcome = outcome
                        error_item = item

            for fut in as_completed(futures):
                record_outcome(fut)
                if interrupt_outcome is not None or error_outcome is not None:
                    # Do not checkpoint immediately.  Other siblings in the same
                    # batch may already have completed successfully; their
                    # updates must be committed and they must be removed from the
                    # resume frontier to preserve the no-rerun invariant.
                    break

            if interrupt_outcome is not None or error_outcome is not None:
                # Cancel work that has not started yet.  Futures already running
                # cannot be safely abandoned: they may perform side effects after
                # we return.  Wait for those running siblings, commit successful
                # updates, and exclude them from the resume frontier.
                for fut in futures:
                    if fut in processed:
                        continue
                    if fut.cancel():
                        continue
                    record_outcome(fut)

            if interrupt_outcome is not None:
                completed_keys = set(updates.keys())
                checkpoint_state = state
                # Merge successful sibling updates in original batch order, not
                # completion order, so partial commits remain deterministic.
                for done_item in batch:
                    done_key = self._item_key(done_item)
                    if done_key in completed_keys:
                        checkpoint_state = self.g.schema.merge(
                            checkpoint_state, updates[done_key]
                        )
                checkpoint_frontier = list(waiting) + [
                    item for item in batch
                    if self._item_key(item) not in completed_keys
                ]
                node = interrupt_item["node"]
                self.cp.put(Checkpoint(thread_id, step - 1, checkpoint_state,
                                       checkpoint_frontier, "interrupted",
                                       interrupt_outcome["payload"]))
                self.cp.log_event(thread_id, "interrupt",
                                  {"node": node,
                                   "instance_id": interrupt_item.get("instance_id"),
                                   "payload": interrupt_outcome["payload"]})
                return RunResult(thread_id, "interrupted", checkpoint_state, step - 1,
                                 interrupt_payload=interrupt_outcome["payload"])

            if error_outcome is not None:
                node = error_item["node"]
                self.cp.put(Checkpoint(
                    thread_id, step - 1, state, current_frontier, "failed"
                ))
                self.cp.log_event(thread_id, "error",
                                  {"node": node,
                                   "instance_id": error_item.get("instance_id"),
                                   "error": error_outcome["error"]})
                return RunResult(thread_id, "failed", state, step - 1,
                                 error=f"{node}: {error_outcome['error']}")
            resume_for = {}  # resume 值只对恢复后的第一个 super-step 有效

            # 2) 合并所有 update（顺序按 batch，确保确定性）
            for item in batch:
                state = self.g.schema.merge(state, updates[self._item_key(item)])

            # 3) 计算下一批 frontier
            next_frontier: list[dict[str, Any]] = list(waiting)
            try:
                for item in batch:
                    next_frontier.extend(self._successor_items(item, state, step))
            except Exception as exc:  # noqa: BLE001 — router/Send 错误需转成运行失败
                self.cp.put(Checkpoint(thread_id, step, state, next_frontier, "failed"))
                self.cp.log_event(thread_id, "error", {"error": str(exc)})
                return RunResult(thread_id, "failed", state, step, error=str(exc))
            frontier = self._dedupe_items(self._resolve_barriers(next_frontier))

            # 4) 落盘本 super-step 的 checkpoint
            status = "running" if frontier else "completed"
            self.cp.put(Checkpoint(thread_id, step, state, frontier, status))

        self.cp.log_event(thread_id, "complete", {"step": step})
        return RunResult(thread_id, "completed", state, step)

    # —— 单节点执行：含重试与 interrupt 捕获 —— #
    def _exec_node(self, thread_id: str, node: _Node, item: dict[str, Any],
                   state: dict[str, Any], step: int,
                   resume_value: Any) -> dict[str, Any]:
        attempt = 0
        run_state = state
        if item.get("arg"):
            run_state = copy.deepcopy(state)
            run_state.update(copy.deepcopy(item.get("arg") or {}))
        while True:
            ctx = NodeContext(node.name, step, attempt, resume_value,
                              thread_id, self.cp, item.get("instance_id") or node.name)
            try:
                update = node.fn(run_state, ctx)
                self.cp.log_event(thread_id, "node_ok",
                                  {"node": node.name,
                                   "instance_id": item.get("instance_id"),
                                   "attempt": attempt})
                return {"kind": "ok", "update": update}
            except Interrupt as it:
                return {"kind": "interrupt", "payload": it.payload}
            except Exception as exc:  # noqa: BLE001 — 引擎需兜住任意节点异常
                if attempt < node.retries:
                    self.cp.log_event(thread_id, "node_retry",
                                      {"node": node.name,
                                       "instance_id": item.get("instance_id"),
                                       "attempt": attempt,
                                       "error": str(exc)})
                    attempt += 1
                    resume_value = _MISSING  # 重试不复用 resume
                    if node.retry_backoff:
                        time.sleep(node.retry_backoff)
                    continue
                return {"kind": "error",
                        "error": f"{exc}\n{traceback.format_exc(limit=2)}"}