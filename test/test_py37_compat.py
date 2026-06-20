"""验证 Python 3.7 兼容性（不需要在 3.7 实际跑，做语法/导入级检查）。

PM 审查结论：实际需要修的只有 3.8+ 才支持的 f-string conversion（!r 等）。
这个测试在当前 3.14 环境下跑，做：
1. AST 语法检查（确保不含 3.8+/3.9+/3.10+ 语法）
2. 关键 dataclass 都能 import
3. subprocess 新 API 在我们 import 链路下可用
4. typing 符号能 import

写代码注意（必须 3.7 兼容）：
- 不使用 match/case（3.10+）
- 不使用 dict | list 联合类型（3.10+）
- 不使用 dict[str, int] 内建泛型（3.9+）
- 不使用 walrus :=（3.8+）
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import unittest
from typing import Any, Dict, List, Optional, Tuple


# —— 3.7+ 新 API 验证 —— #

def test_subprocess_new_api() -> None:
    """subprocess.run 的 capture_output/text/timeout 是 3.7+ 才加的。

    在 3.7 之前要用 stdout=PIPE, stderr=PIPE, universal_newlines=True。
    我们用 3.7+ 新写法，必须能跑通。
    """
    result = subprocess.run(
        ["echo", "hi"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "hi"


def test_typing_symbols() -> None:
    """typing 模块符号验证 —— 全部 3.5+ 已支持。"""
    from typing import Any, Dict, List, Optional, Tuple, Union  # noqa: F401
    # 3.7+ 支持的泛型订阅
    x: Optional[Dict[str, List[Tuple[int, str]]]] = None
    assert x is None
    # 3.7+ 支持的 typing.Any
    y: Any = "anything"
    assert y == "anything"


def test_dataclass_imports() -> None:
    """所有公开 dataclass 都能从 agentflow import 出来。"""
    from agentflow.checkpoint import Checkpoint  # noqa: F401
    from agentflow.graph import NodeContext, RunResult, ValidationIssue  # noqa: F401
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


# —— AST 检查：确保源码不含 3.8+ 才支持的语法 —— #

# 需要检查的目录
_SOURCE_DIRS = ["agentflow", "test", "demo.py", "demo"]


def _walk_py_files() -> List[str]:
    """收集所有需要检查的 .py 文件路径。"""
    files: List[str] = []
    for entry in _SOURCE_DIRS:
        if os.path.isfile(entry):
            files.append(entry)
            continue
        for root, _dirs, names in os.walk(entry):
            for n in names:
                if n.endswith(".py"):
                    files.append(os.path.join(root, n))
    return files


def test_no_py38_fstring_debug() -> None:
    """扫描 f-string 调试语法 f'{var=}' —— 3.8+ 才支持。

    注意要跳过 f-string 内部的 {x=...} 嵌套（如 set 类型）—— 但 3.7 根本没有
    f-string 调试语法，所以任何 {var=...} 出现在 f-string 顶层都是问题。
    简化方案：找出所有 f-string，逐一检查其中是否有 [a-z_]+= 模式。
    """
    import re
    # 注意：f-string 必须以 f" 或 f' 开头，字符串内可能含 \{...} 转义
    pat = re.compile(r"""f['"][^'"]*\{[a-zA-Z_][a-zA-Z0-9_]*\s*=""")
    for path in _walk_py_files():
        # 跳过本测试文件自身的文档字符串（含 f'{var=}' 示例）
        if path.endswith("test_py37_compat.py"):
            continue
        with open(path, "r", encoding="utf-8") as fp:
            for i, line in enumerate(fp, 1):
                # 跳过纯注释行
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if pat.search(line):
                    raise AssertionError(
                        f"{path}:{i} 含 3.8+ f-string 调试语法: {line.rstrip()}"
                    )


def test_no_py38_fstring_conversion() -> None:
    """扫描 f-string conversion f'{var!r}' / '{var!s}' / '{var!a}' —— 3.8+ 才支持。

    CR 2026-06-18 1.2: 用 AST 代替 regex 避免 false negative（regex 会被
    f-string 内的单引号切断）。AST.FormattedValue.conversion 是 int:
    -1=无, 114=repr(!r), 115=str(!s), 97=ascii(!a)
    """
    for path in _walk_py_files():
        if path.endswith("test_py37_compat.py"):
            continue
        with open(path, "r", encoding="utf-8") as fp:
            src = fp.read()
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError as e:
            raise AssertionError(f"{path} 解析失败: {e}")
        for node in ast.walk(tree):
            if isinstance(node, ast.FormattedValue) and node.conversion != -1:
                raise AssertionError(
                    f"{path}:{node.lineno} 含 3.8+ f-string conversion "
                    f"(conversion={node.conversion}, 应改为 repr(...))"
                )


def test_no_py38_walrus() -> None:
    """扫描 walrus operator := —— 3.8+ 才支持。

    在 3.7 下 `ast.NamedExpr` 不存在，所以此测试变为空操作（3.7 本身无法
    解析 walrus 语法，如果真有 walrus 会在 ast.parse 时 SyntaxError）。
    """
    if not hasattr(ast, "NamedExpr"):
        # 3.7 无此节点类型 — 如果有 walrus，ast.parse 会抛 SyntaxError
        return
    for path in _walk_py_files():
        with open(path, "r", encoding="utf-8") as fp:
            src = fp.read()
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError as e:
            raise AssertionError(f"{path} 语法错误: {e}")
        for node in ast.walk(tree):
            if isinstance(node, ast.NamedExpr):
                raise AssertionError(
                    f"{path}:{node.lineno} 使用了 walrus operator (3.8+)"
                )


def test_no_py39_pep585() -> None:
    """用 AST 检测 PEP 585 内建泛型订阅 list[int] —— 3.9+ 才支持。

    CR 2026-06-18 2.2（Round 3 修正）: 原实现误以为 `from __future__ import
    annotations` 会让注解在 AST 中变成 Constant 字符串，于是对带 future
    import 的文件"全量报告 Subscript"。但 PEP 563 的字符串化发生在运行时
    求值阶段（typing.get_type_hints），ast.parse 阶段注解仍是 Subscript
    节点。这导致任何 future+list[int] 注解的文件被误报。

    正确做法：无论是否有 future import，统一用 parent_map 判断 Subscript
    是否在注解位置（AnnAssign.annotation / arg.annotation / FunctionDef.
    returns）。注解位置的 list[int] 在 3.7 下不会被求值（有 future 保护），
    是安全的；只有运行时上下文（isinstance(x, list[int])、cast(...) 等）
    才是真正的违规。
    """
    _PEP585_NAMES = frozenset({"list", "dict", "set", "frozenset", "type"})
    for path in _walk_py_files():
        with open(path, "r", encoding="utf-8") as fp:
            src = fp.read()
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError as e:
            raise AssertionError(f"{path} 语法错误: {e}")
        # 构建 parent 映射，用于判断 Subscript 是否在注解上下文
        parent_map: dict = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parent_map[child] = parent

        def _is_annotation_context(node: ast.Subscript) -> bool:
            """判断 Subscript 是否用作类型注解（运行时不会被求值）。

            沿父链上溯：类型表达式中间可能出现的合法节点（Subscript / Tuple /
            BinOp(BitOr) / Attribute / Constant(None)）继续往上找，直到命中
            注解节点或其它上下文为止。这覆盖嵌套场景，如
            Dict[str, list[int]] 里 list[int] 的父链是 Tuple → Subscript(Dict)
            → AnnAssign。
            """
            cur = node
            while cur in parent_map:
                parent = parent_map[cur]
                # 变量注解: y: list[int] = []
                if isinstance(parent, ast.AnnAssign) and parent.annotation is cur:
                    return True
                # 函数参数注解: def f(x: list[int])
                if isinstance(parent, ast.arg) and parent.annotation is cur:
                    return True
                # 返回类型注解: def f() -> list[int]
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) and parent.returns is cur:
                    return True
                # 类型表达式中间节点：继续上溯
                if isinstance(parent, ast.Subscript):
                    cur = parent
                    continue
                if isinstance(parent, ast.Tuple):  # Dict[k, v] 的 slice
                    cur = parent
                    continue
                if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.BitOr):
                    cur = parent
                    continue
                if isinstance(parent, ast.Attribute):
                    cur = parent
                    continue
                return False
            return False

        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript):
                if isinstance(node.value, ast.Name) and node.value.id in _PEP585_NAMES:
                    if _is_annotation_context(node):
                        continue  # 注解位置，3.7 下不求值，安全
                    raise AssertionError(
                        f"{path}:{node.lineno} 含 PEP 585 内建泛型 "
                        f"{node.value.id}[...] (3.9+)"
                    )


def test_no_py310_match_case() -> None:
    """扫描 match/case 语法 —— 3.10+ 才支持。

    在 3.7-3.9 下 `ast.Match` 不存在，如果有 match/case 会在 ast.parse 时
    SyntaxError（因为 match 在 3.7 下不是关键字，但 case 也不是，所以实际上
    不会 parse 失败——`match = 1` 是合法的。此测试在高版本下用 AST 检查）。
    """
    if not hasattr(ast, "Match"):
        # 3.7-3.9 无此节点类型，match/case 语法本身在 3.7 下不合法
        return
    for path in _walk_py_files():
        with open(path, "r", encoding="utf-8") as fp:
            src = fp.read()
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError as e:
            raise AssertionError(f"{path} 语法错误: {e}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Match):
                raise AssertionError(
                    f"{path}:{node.lineno} 使用了 match/case (3.10+)"
                )


def test_no_py310_union_pipe() -> None:
    """用 AST 检测 PEP 604 X | Y 联合类型 —— 3.10+ 才支持。

    CR 2026-06-18 2.1（Round 3 修正）: 原实现用正则匹配含 ':' 或 '->' 的行，
    漏检运行时类型别名 `MyType = int | str`（无 ':'/'->' 前缀，但 3.7 运行
    时会 TypeError）。正则方案的根本缺陷：无法区分注解 union 与位运算。

    改用 AST：找到所有 `BinOp(op=BitOr)` 且两侧都是 type-like 的 union，
    再判断它是否出现在「该报告」的上下文：
      1. 类型注解（AnnAssign.annotation / arg.annotation / FunctionDef.returns）
         —— 在 3.7 下若有 from __future__ import annotations 则不求值、安全；
         但若无 future 保护则运行时求值会炸，统一报告（保守）。
      2. 类型别名赋值（`PascalName = Type | Type`）—— 运行时求值，3.7 必炸。
    不报告普通位运算（flags | bits、set | set），靠左侧是否 PascalCase
    类型名 + 右侧是否 type-like 双重过滤。
    """
    _BUILTIN_TYPES = frozenset({
        "int", "str", "float", "bool", "bytes", "complex",
        "list", "dict", "set", "tuple", "frozenset", "type",
    })

    def _is_type_like(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return (
                node.id in _BUILTIN_TYPES
                or node.id == "None"
                or node.id[0].isupper()  # PascalCase / SCREAMING_SNAKE 约定
            )
        if isinstance(node, ast.Constant) and node.value is None:
            return True
        if isinstance(node, ast.Attribute):  # typing.Optional, mod.Type
            return True
        if isinstance(node, ast.Subscript):
            return _is_type_like(node.value)
        return False

    def _is_union_binop(node: ast.AST) -> bool:
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.BitOr):
            return False
        left_ok = _is_type_like(node.left) or _is_union_binop(node.left)
        right_ok = _is_type_like(node.right) or _is_union_binop(node.right)
        return left_ok and right_ok

    def _is_type_alias_target(name: str) -> bool:
        # PascalCase 类型名：首字母大写且不全大写（排除 SCREAMING_SNAKE_CASE 常量）
        return name[0].isupper() and not name.isupper()

    for path in _walk_py_files():
        with open(path, "r", encoding="utf-8") as fp:
            src = fp.read()
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError as e:
            raise AssertionError(f"{path} 语法错误: {e}")
        parent_map: dict = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parent_map[child] = parent
        for node in ast.walk(tree):
            if not _is_union_binop(node):
                continue
            # 沿父链判断是否在「该报告」的上下文
            cur = node
            reported = False
            while cur in parent_map:
                parent = parent_map[cur]
                # 类型注解上下文
                if isinstance(parent, ast.AnnAssign) and parent.annotation is cur:
                    reported = True
                    break
                if isinstance(parent, ast.arg) and parent.annotation is cur:
                    reported = True
                    break
                if (isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and parent.returns is cur):
                    reported = True
                    break
                # 类型别名赋值: PascalName = (Type | Type)
                if isinstance(parent, ast.Assign):
                    if (len(parent.targets) == 1
                            and isinstance(parent.targets[0], ast.Name)
                            and parent.value is cur
                            and _is_type_alias_target(parent.targets[0].id)):
                        reported = True
                    break  # 其它赋值（位运算等）不报，停止上溯
                cur = parent
            if reported:
                raise AssertionError(
                    f"{path}:{node.lineno} 含 PEP 604 X | Y 联合类型 (3.10+)"
                )


# —— apply_patch 错误信息格式验证 —— #

def test_apply_patch_error_msg_uses_repr() -> None:
    """验证 agentflow/tools.py 的错误信息用 repr() 而非 !r conversion。

    这是 PM 审查发现的关键点：如果保留 f"{path!r}"，在 3.7 下 SyntaxError。
    我们用 3.7 兼容的 f"{repr(path)}"。
    """
    from agentflow.tools import ToolRuntime
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        rt = ToolRuntime(thread_id="compat_test", root=tmp)
        try:
            rt.apply_patch("x.py", "")
        except ValueError as e:
            msg = str(e)
            # 必须含 path 的 repr（带引号）
            assert "'x.py'" in msg, f"apply_patch 错误信息应含 path 的 repr，实际: {msg}"
            return
        raise AssertionError("应抛 ValueError")


# —— TestRunner 入口 —— #

class _Py37CompatTests(unittest.TestCase):
    """所有测试的 unittest 包装，方便用 pytest 跑。"""

    def test_subprocess(self) -> None:
        test_subprocess_new_api()

    def test_typing(self) -> None:
        test_typing_symbols()

    def test_dataclass_imports(self) -> None:
        test_dataclass_imports()

    def test_no_fstring_debug(self) -> None:
        test_no_py38_fstring_debug()

    def test_no_fstring_conversion(self) -> None:
        test_no_py38_fstring_conversion()

    def test_no_walrus(self) -> None:
        test_no_py38_walrus()

    def test_no_pep585(self) -> None:
        test_no_py39_pep585()

    def test_no_match_case(self) -> None:
        test_no_py310_match_case()

    def test_no_union_pipe(self) -> None:
        test_no_py310_union_pipe()

    def test_apply_patch_error(self) -> None:
        test_apply_patch_error_msg_uses_repr()


if __name__ == "__main__":
    # 直接以独立函数跑（不依赖 pytest）
    funcs = [
        ("test_subprocess_new_api", test_subprocess_new_api),
        ("test_typing_symbols", test_typing_symbols),
        ("test_dataclass_imports", test_dataclass_imports),
        ("test_no_py38_fstring_debug", test_no_py38_fstring_debug),
        ("test_no_py38_fstring_conversion", test_no_py38_fstring_conversion),
        ("test_no_py38_walrus", test_no_py38_walrus),
        ("test_no_py39_pep585", test_no_py39_pep585),
        ("test_no_py310_match_case", test_no_py310_match_case),
        ("test_no_py310_union_pipe", test_no_py310_union_pipe),
        ("test_apply_patch_error_msg_uses_repr", test_apply_patch_error_msg_uses_repr),
    ]
    failed = 0
    for name, fn in funcs:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
    if failed:
        print(f"\n{failed}/{len(funcs)} 测试失败")
        sys.exit(1)
    print(f"\n全部 {len(funcs)} 个测试通过")
