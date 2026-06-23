"""Shared graph state + reducer-based merge.

设计对应调研报告「LangGraph 的 StateGraph + checkpointer」：每个节点接收当前
state、返回一个「部分更新（partial update）」，由 reducer 合并回全局 state。
这样节点之间无需共享可变对象，状态演进是可追溯的（事件溯源的基础）。
"""

import copy
from dataclasses import dataclass, field
from typing import Any, Callable


# reducer 决定「同一个 key 的新值如何并入旧值」。
# 默认 reducer = 覆盖；list 类型的 key 可用 append_reducer 累积（如多分支汇聚）。
Reducer = Callable[[Any, Any], Any]


def overwrite_reducer(_old: Any, new: Any) -> Any:
    """默认：新值直接覆盖旧值。"""
    return new


def append_reducer(old: Any, new: Any) -> Any:
    """把新值追加到列表（用于并行分支汇聚 / 累积日志）。"""
    base = list(old) if old else []
    if isinstance(new, list):
        base.extend(new)
    else:
        base.append(new)
    return base


def fanout_reducer(old: Any, new: Any) -> Any:
    """合并动态 worker 产出，约定 new 是 {instance_id: payload} 映射。"""
    base = dict(old) if old else {}
    if not new:
        return base
    if not isinstance(new, dict):
        raise TypeError("fanout_reducer 只接受 dict 更新")
    base.update(new)
    return base


@dataclass
class StateSchema:
    """声明 state 的 key 与各自的 reducer。未声明的 key 默认用覆盖语义。"""

    reducers: dict[str, Reducer] = field(default_factory=dict)

    def reducer_for(self, key: str) -> Reducer:
        return self.reducers.get(key, overwrite_reducer)

    def merge(self, state: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
        """把节点返回的 partial update 并入 state，返回一份新 dict（不原地修改）。"""
        if not update:
            return state
        merged = copy.deepcopy(state)
        for key, new_value in update.items():
            merged[key] = self.reducer_for(key)(merged.get(key), new_value)
        return merged