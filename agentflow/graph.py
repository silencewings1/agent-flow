"""DAG 引擎：StateGraph + 超步（super-step）执行器。

对应报告核心结论：
- 节点 = 函数（add_node），边 = add_edge，条件边 = add_conditional_edges（返回下一节点名）；
- 超步执行：每一步把当前 frontier 里的节点**并行**跑完，再算出下一批 frontier；
- 支持循环（条件边可指回上游，如测试失败回到 Coder）——故是「有向图」而非严格无环；
- 节点级重试 + interrupt 暂停 + checkpoint 恢复。

START / END 是两个保留节点名，分别表示入口与出口。
"""
from __future__ import annotations

import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .checkpoint import Checkpoint, Checkpointer
from .interrupt import Command, Interrupt, _MISSING
from .state import StateSchema

START = "__start__"
END = "__end__"

# 节点签名：接收 (state, ctx) → 返回 partial update（dict）或 None。
NodeFn = Callable[[Dict[str, Any], "NodeContext"], Optional[Dict[str, Any]]]
# 条件边路由函数：接收 state → 返回下一个（或多个）节点名。
RouterFn = Callable[[Dict[str, Any]], Any]


@dataclass
class NodeContext:
    """传给节点的运行时上下文。"""

    node: str
    step: int
    attempt: int
    resume_value: Any = _MISSING  # 恢复时注入的人工值；正常执行为 _MISSING

    def interrupt(self, payload: Any) -> Any:
        from .interrupt import interrupt as _interrupt
        return _interrupt(payload, self.resume_value)


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
            ctx = NodeContext(node.name, step, attempt, resume_value)
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
