"""agentflow —— 最小 DAG 编排内核（自研，零三方依赖）。

落地调研报告的核心结论：
- StateGraph：节点=函数 / 边 / 条件边（支持循环），super-step 并行执行；
- Checkpointer：事件溯源式持久化，恢复时不重跑已完成节点；
- interrupt / Command：人在回路暂停与恢复。
"""
from .graph import (
    CompiledGraph,
    NodeContext,
    RunResult,
    Send,
    StateGraph,
    START,
    END,
)
from .state import StateSchema, append_reducer, fanout_reducer, overwrite_reducer
from .checkpoint import Checkpoint, Checkpointer
from .interrupt import Command, Interrupt
from .llm import LLMRegistry, NodeLLMConfig
from .plan import Plan, parse_plan_from_llm
from .tools import ToolRuntime
from .tools import MCPToolProvider
from .graph_config import (
    build_graph_from_config,
    build_state_graph_from_config,
    load_graph_config,
)

__all__ = [
    "StateGraph",
    "CompiledGraph",
    "NodeContext",
    "RunResult",
    "Send",
    "START",
    "END",
    "StateSchema",
    "append_reducer",
    "fanout_reducer",
    "overwrite_reducer",
    "Checkpoint",
    "Checkpointer",
    "Command",
    "Interrupt",
    "LLMRegistry",
    "MCPToolProvider",
    "NodeLLMConfig",
    "Plan",
    "parse_plan_from_llm",
    "ToolRuntime",
    "load_graph_config",
    "build_graph_from_config",
    "build_state_graph_from_config",
]
