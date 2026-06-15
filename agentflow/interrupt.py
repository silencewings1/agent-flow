"""HITL 与恢复用的控制原语。

对应报告「人在回路（interrupt）」与「错误恢复」两节：
- Interrupt：节点主动抛出，暂停整张图，把待人工处理的载荷写进 checkpoint。
- Command：恢复时由外部传入，携带人工给出的 resume 值，注入回被中断的节点。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class Interrupt(Exception):
    """节点内调用 interrupt() 抛出，引擎据此暂停并持久化。

    被中断的线程除存储外不占用计算资源（对应报告中 LangGraph 的 interrupt 语义）。
    """

    def __init__(self, payload: Any):
        super().__init__("graph interrupted")
        self.payload = payload


@dataclass
class Command:
    """恢复指令：resume 是人工对上次 interrupt 的回应值。"""

    resume: Any = None


def interrupt(payload: Any, resume_value: Any) -> Any:
    """在节点内请求人工介入。

    语义模仿 Python input() 但面向持久化执行：
    - 首次执行（resume_value 为 _MISSING）→ 抛 Interrupt，引擎暂停并存盘；
    - 恢复执行（外部经 Command 注入了 resume_value）→ 直接返回该值，节点继续往下跑。
    """
    if resume_value is _MISSING:
        raise Interrupt(payload)
    return resume_value


class _Missing:
    """哨兵：区分『没有 resume 值』与『resume 值恰好是 None』。"""

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return "<MISSING>"


_MISSING = _Missing()
