"""Python 3.14 现代语法兼容性检查（历史文件，已迁移到 test_py314_compat.py）。

本文件保留基本导入检查，AST 现代语法验证已移至 test_py314_compat.py。
"""
import ast
import os
import subprocess
import sys
import unittest
from typing import Any


def test_subprocess_new_api() -> None:
    """subprocess.run 的 capture_output/text/timeout 是 3.7+ 才加的。"""
    result = subprocess.run(
        ["echo", "hi"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "hi"


def test_typing_symbols() -> None:
    """现代 typing 验证 —— 使用内置泛型和联合类型。"""
    from typing import Any, Callable  # noqa: F401
    x: list[str | None] = ["a", None, "b"]
    assert x == ["a", None, "b"]
    d: dict[str, int] = {"a": 1}
    assert d == {"a": 1}
    y: Any = "anything"
    assert y == "anything"
    z: Callable[[int], str] = str
    assert z(1) == "1"


def test_dataclass_imports() -> None:
    """所有公开 dataclass 都能从 agentflow import 出来。"""
    from agentflow.checkpoint import Checkpoint  # noqa: F401
    from agentflow.graph import NodeContext, RunResult  # noqa: F401
    from agentflow.interrupt import Command, Interrupt  # noqa: F401
    from agentflow.llm import LLMRegistry, NodeLLMConfig  # noqa: F401
    from agentflow.plan import Plan  # noqa: F401
    from agentflow.state import StateSchema  # noqa: F401
    from agentflow.tools import ToolRuntime  # noqa: F401
    from agentflow import (  # noqa: F401
        Checkpointer,
        Command,
        CompiledGraph,
        END,
        Interrupt,
        LLMRegistry,
        Plan,
        START,
        StateGraph,
        StateSchema,
        ToolRuntime,
        append_reducer,
        overwrite_reducer,
    )


ALL_TESTS = [
    test_subprocess_new_api,
    test_typing_symbols,
    test_dataclass_imports,
]


if __name__ == "__main__":
    failed = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"✅ {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"❌ {test.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"💥 {test.__name__}: {type(exc).__name__}: {exc}")
    if failed:
        sys.exit(1)
    print(f"\n✅ 全部 {len(ALL_TESTS)} 个基本兼容性检查通过\n")
