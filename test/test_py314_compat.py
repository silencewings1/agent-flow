"""验证 Python 3.14 现代语法（不需要在 3.14 实际跑，做语法/导入级检查）。

本测试在当前 3.14 环境下跑，做：
1. AST 语法检查（确保使用 3.9+/3.10+/3.12+ 现代语法）
2. 关键 dataclass 都能 import
3. subprocess 新 API 在我们 import 链路下可用
4. typing 符号能 import

强制现代语法要求：
- 使用 match/case（3.10+）
- 使用 X | Y 联合类型（3.10+）
- 使用 list[str] 内建泛型（3.9+）
- 使用 walrus :=（3.8+）
- 使用 f-string 调试语法 f'{var=}'（3.8+）
- 移除 from __future__ import annotations（3.9+ 不再需要）
"""

import ast
import os
import re
import subprocess
import sys
import unittest
from typing import Any


# —— 3.14+ 新 API 验证 —— #

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
    # 现代语法：使用内置泛型，不导入 Dict/List/Tuple/Optional/Union
    x: list[str | None] = ["a", None, "b"]
    assert x == ["a", None, "b"]
    # dict[str, int] 内建泛型
    d: dict[str, int] = {"a": 1}
    assert d == {"a": 1}
    # tuple[int, str] 内建泛型
    t: tuple[int, str] = (1, "a")
    assert t == (1, "a")


def test_dataclass_imports() -> None:
    """所有公开 dataclass 都能从 agentflow import 出来。"""
    from agentflow.checkpoint import Checkpoint  # noqa: F401
    from agentflow.graph import NodeContext, RunResult  # noqa: F401
    from agentflow.interrupt import Command, Interrupt  # noqa: F401
    from agentflow.llm import LLMRegistry, NodeLLMConfig  # noqa: F401
    from agentflow.plan import Plan  # noqa: F401
    from agentflow.state import StateSchema  # noqa: F401
    from agentflow.tools import ToolRuntime  # noqa: F401
    # 主入口也确认
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


# —— AST 检查：确保源码使用现代语法 —— #

# 需要检查的目录
_SOURCE_DIRS = ["agentflow", "test", "demo.py", "demo"]


def _walk_py_files() -> list[str]:
    """收集所有需要检查的 .py 文件路径。"""
    files: list[str] = []
    for entry in _SOURCE_DIRS:
        if os.path.isfile(entry):
            files.append(entry)
            continue
        for root, _dirs, names in os.walk(entry):
            for n in names:
                if n.endswith(".py"):
                    files.append(os.path.join(root, n))
    return files


def test_no_future_annotations() -> None:
    """确保源码不含 from __future__ import annotations（3.9+ 不再需要）。"""
    for path in _walk_py_files():
        if path.endswith("test_py314_compat.py"):
            continue
        with open(path, "r", encoding="utf-8") as fp:
            for i, line in enumerate(fp, 1):
                if line.strip() == "from __future__ import annotations":
                    raise AssertionError(
                        f"{path}:{i}: 发现旧式 from __future__ import annotations，"
                        "请移除并使用原生 PEP 585 内建泛型"
                    )


def test_no_old_typing_imports() -> None:
    """确保源码不含 Dict/List/Tuple/Optional/Union 导入（应使用内置泛型 / | None）。"""
    old_imports = {"Dict", "List", "Tuple", "Optional", "Union"}
    for path in _walk_py_files():
        # 跳过旧式 3.7 兼容测试文件（历史保留）
        if path.endswith("test_py37_compat.py") or path.endswith("test_py314_compat.py"):
            continue
        with open(path, "r", encoding="utf-8") as fp:
            for i, line in enumerate(fp, 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if stripped.startswith("from typing import "):
                    names_str = stripped[len("from typing import "):]
                    names = {n.strip() for n in names_str.split(",")}
                    bad = names & old_imports
                    if bad:
                        raise AssertionError(
                            f"{path}:{i}: 发现旧式 typing 导入 {sorted(bad)}，"
                            "请改用内置泛型或 | None / X | Y"
                        )


def test_match_case_used() -> None:
    """确保核心源码使用 match/case（至少 agentflow/graph.py 需要）。"""
    required = ["agentflow/graph.py"]
    for path in required:
        with open(path, "r", encoding="utf-8") as fp:
            source = fp.read()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            raise AssertionError(f"{path}: 无法解析为 Python 语法")
        found = any(isinstance(node, ast.Match) for node in ast.walk(tree))
        assert found, f"{path}: 未找到 match/case 语句，请重构条件分支"


def test_walrus_used() -> None:
    """确保核心源码使用 walrus :=（至少 agentflow 中有使用）。"""
    required = ["agentflow/graph.py", "agentflow/nodes.py", "agentflow/llm.py"]
    found_any = False
    for path in required:
        with open(path, "r", encoding="utf-8") as fp:
            source = fp.read()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            raise AssertionError(f"{path}: 无法解析为 Python 语法")
        if any(isinstance(node, ast.NamedExpr) for node in ast.walk(tree)):
            found_any = True
    assert found_any, "agentflow 核心模块中未找到 walrus := 表达式"


def test_pep604_union_used() -> None:
    """确保核心源码使用 X | Y 联合类型。"""
    required = ["agentflow/graph.py", "agentflow/llm.py"]
    for path in required:
        with open(path, "r", encoding="utf-8") as fp:
            source = fp.read()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            raise AssertionError(f"{path}: 无法解析为 Python 语法")
        has_union = False
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                has_union = True
                break
        assert has_union, f"{path}: 未找到 X | Y 联合类型（PEP 604）"


def test_pep585_builtin_generics_used() -> None:
    """确保源码使用 list[str] / dict[str, int] 等内建泛型。"""
    required = ["agentflow/graph.py", "agentflow/state.py", "agentflow/checkpoint.py"]
    for path in required:
        with open(path, "r", encoding="utf-8") as fp:
            source = fp.read()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            raise AssertionError(f"{path}: 无法解析为 Python 语法")
        has_builtin_generic = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript):
                # list[str], dict[str, int], tuple[int, str]
                if isinstance(node.value, ast.Name):
                    if node.value.id in {"list", "dict", "tuple", "set", "frozenset"}:
                        has_builtin_generic = True
                        break
        assert has_builtin_generic, f"{path}: 未找到内建泛型 subscript（PEP 585）"


ALL_TESTS = [
    test_subprocess_new_api,
    test_typing_symbols,
    test_dataclass_imports,
    test_no_future_annotations,
    test_no_old_typing_imports,
    test_match_case_used,
    test_walrus_used,
    test_pep604_union_used,
    test_pep585_builtin_generics_used,
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
    print(f"\n✅ 全部 {len(ALL_TESTS)} 个 Python 3.14 现代语法测试通过\n")
