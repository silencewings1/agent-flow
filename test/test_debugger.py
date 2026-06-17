"""P1-4 真实 Debugger 测试：pytest 调用 + 失败解析 + 回环。

验证 debugger 节点能真实执行 pytest、解析失败输出、以及 fallback 到旧行为。
"""
from __future__ import annotations

import os
import sys
import tempfile

# 确保 agentflow 在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentflow import Checkpointer, Command, StateGraph, StateSchema, START, END, append_reducer
from agentflow.nodes import debugger, route_after_debug


def _setup_src(workdir: str) -> str:
    """在 workdir 下创建 src/ 目录并写入 __init__.py。返回 src_dir 路径。"""
    src_dir = os.path.join(workdir, "src")
    os.makedirs(src_dir, exist_ok=True)
    # 必须有 __init__.py 才能做 `from src.task_t1 import ...`
    with open(os.path.join(src_dir, "__init__.py"), "w") as f:
        f.write("# auto-generated package\n")
    return src_dir


def _run_debugger(state, thread_id="test-dbg"):
    """在最小图里跑一次 debugger 节点，返回 RunResult。"""
    cp = Checkpointer()
    schema = StateSchema(reducers={"log": append_reducer, "artifacts": append_reducer})
    g = StateGraph(schema, max_steps=10)
    g.add_node("debugger", debugger)
    g.add_edge(START, "debugger")
    g.add_edge("debugger", END)
    return g.compile(cp).invoke(state, thread_id=thread_id)


def test_debugger_no_test_files():
    """workdir 无测试文件 → tests_passed=True，log 提示未发现测试。"""
    with tempfile.TemporaryDirectory() as td:
        src_dir = _setup_src(td)
        # 只有实现文件，没有 test_*.py
        with open(os.path.join(src_dir, "task_t1.py"), "w") as f:
            f.write("def add(a, b): return a + b\n")

        res = _run_debugger({
            "tasks": ["t1"],
            "code_version": 1,
            "workdir": td,
            "artifacts": ["src/task_t1.py"],
        }, thread_id="no-tests")
        assert res.status == "completed", res.status
        s = res.state
        assert s["tests_passed"] is True, f"expected tests_passed=True, got {s['tests_passed']}"
        assert s["test_failures"] == [], f"expected empty failures, got {s['test_failures']}"
        assert "未发现测试" in s["test_report"], f"expected '未发现测试' in report: {s['test_report']}"
        assert any("未发现测试" in l for l in s.get("log", [])), f"log should mention no tests: {s.get('log')}"
        print("✅ test_debugger_no_test_files 通过")


def test_debugger_all_pass():
    """workdir 有测试文件且全部通过 → tests_passed=True, exit_code=0。"""
    with tempfile.TemporaryDirectory() as td:
        src_dir = _setup_src(td)
        with open(os.path.join(src_dir, "task_t1.py"), "w") as f:
            f.write("def add(a, b): return a + b\n")
        with open(os.path.join(src_dir, "test_add.py"), "w") as f:
            f.write("from src.task_t1 import add\n"
                    "def test_add_pos():\n    assert add(2, 3) == 5\n"
                    "def test_add_neg():\n    assert add(-1, 1) == 0\n")

        res = _run_debugger({
            "tasks": ["t1"],
            "code_version": 1,
            "workdir": td,
            "artifacts": ["src/task_t1.py"],
        }, thread_id="all-pass")
        assert res.status == "completed", res.status
        s = res.state
        assert s["tests_passed"] is True, f"expected tests_passed=True, got {s['tests_passed']}"
        assert s["test_failures"] == [], f"expected empty failures, got {s['test_failures']}"
        assert "全部通过" in s["test_report"], f"expected '全部通过' in report: {s['test_report']}"
        print("✅ test_debugger_all_pass 通过")


def test_debugger_some_fail():
    """workdir 有失败测试 → tests_passed=False, test_failures 非空。"""
    with tempfile.TemporaryDirectory() as td:
        src_dir = _setup_src(td)
        with open(os.path.join(src_dir, "task_t1.py"), "w") as f:
            f.write("def add(a, b): return a - b  # bug: 减法而不是加法\n")
        with open(os.path.join(src_dir, "test_add.py"), "w") as f:
            f.write("from src.task_t1 import add\n"
                    "def test_add_pos():\n    assert add(2, 3) == 5\n"
                    "def test_add_neg():\n    assert add(-1, 1) == 0\n")

        res = _run_debugger({
            "tasks": ["t1"],
            "code_version": 1,
            "workdir": td,
            "artifacts": ["src/task_t1.py"],
        }, thread_id="some-fail")
        assert res.status == "completed", res.status
        s = res.state
        assert s["tests_passed"] is False, f"expected tests_passed=False, got {s['tests_passed']}"
        assert len(s["test_failures"]) > 0, f"expected non-empty failures, got {s['test_failures']}"
        print("✅ test_debugger_some_fail 通过")


def test_debugger_failure_structure():
    """test_failures[0] 含 test_name 和 error_msg 字段。"""
    with tempfile.TemporaryDirectory() as td:
        src_dir = _setup_src(td)
        with open(os.path.join(src_dir, "task_t1.py"), "w") as f:
            f.write("def add(a, b): return a - b  # bug\n")
        with open(os.path.join(src_dir, "test_add.py"), "w") as f:
            f.write("from src.task_t1 import add\n"
                    "def test_add_pos():\n    assert add(2, 3) == 5\n")

        res = _run_debugger({
            "tasks": ["t1"],
            "code_version": 1,
            "workdir": td,
            "artifacts": ["src/task_t1.py"],
        }, thread_id="struct")
        assert res.status == "completed", res.status
        s = res.state
        failures = s["test_failures"]
        assert len(failures) > 0, f"expected failures, got {failures}"
        f0 = failures[0]
        assert isinstance(f0, dict), f"failure should be dict, got {type(f0)}"
        assert "test_name" in f0, f"missing test_name in {f0}"
        assert "error_msg" in f0, f"missing error_msg in {f0}"
        assert f0["test_name"], "test_name should not be empty"
        print(f"✅ test_debugger_failure_structure 通过: {f0}")


def test_debugger_fallback_old_behavior():
    """无 workdir 时走 pass_at_version 旧逻辑（兼容 scenario 1-5）。"""
    # 无 workdir
    res = _run_debugger({
        "tasks": ["t1", "t2"],
        "code_version": 2,
        "pass_at_version": 3,
    }, thread_id="fallback-1")
    assert res.status == "completed", res.status
    s = res.state
    assert s["tests_passed"] is False, f"v2 < pass_at_version=3, should fail"

    res2 = _run_debugger({
        "tasks": ["t1", "t2"],
        "code_version": 3,
        "pass_at_version": 3,
    }, thread_id="fallback-2")
    assert res2.state["tests_passed"] is True, "v3 >= pass_at_version=3, should pass"

    # 有 workdir 但无 artifacts
    with tempfile.TemporaryDirectory() as td:
        res3 = _run_debugger({
            "tasks": ["t1"],
            "code_version": 1,
            "workdir": td,
            "artifacts": [],
            "pass_at_version": 3,
        }, thread_id="fallback-3")
        assert res3.state["tests_passed"] is False, "no artifacts, fallback to pass_at_version"

    print("✅ test_debugger_fallback_old_behavior 通过")


def test_debugger_loop_max_steps():
    """故意失败 + coder 不修 → max_steps 兜底（不无限循环）。"""
    with tempfile.TemporaryDirectory() as td:
        src_dir = _setup_src(td)
        with open(os.path.join(src_dir, "task_t1.py"), "w") as f:
            f.write("def add(a, b): return a - b  # bug\n")
        with open(os.path.join(src_dir, "test_add.py"), "w") as f:
            f.write("from src.task_t1 import add\n"
                    "def test_add_pos():\n    assert add(2, 3) == 5\n")

        cp = Checkpointer()
        schema = StateSchema(reducers={"log": append_reducer, "artifacts": append_reducer})
        g = StateGraph(schema, max_steps=5)  # 小 max_steps 快速触发
        g.add_node("debugger", debugger)
        g.add_conditional_edges("debugger", route_after_debug)
        g.add_edge(START, "debugger")

        # dummy coder：不改文件，只递增版本（永远修不好 bug）
        def dummy_coder(state, ctx):
            v = state.get("code_version", 0) + 1
            return {"code_version": v, "log": [f"[Coder] 修复 v{v}"]}

        g.add_node("coder", dummy_coder)
        g.add_edge("coder", "debugger")
        app = g.compile(cp)

        res = app.invoke({
            "tasks": ["t1"],
            "code_version": 1,
            "workdir": td,
            "artifacts": ["src/task_t1.py"],
        }, thread_id="loop-max")
        # 应该因为 max_steps 触发 failed
        assert res.status == "failed", f"expected failed due to max_steps, got {res.status}"
        assert "max_steps" in (res.error or ""), f"error should mention max_steps: {res.error}"
        print(f"✅ test_debugger_loop_max_steps 通过: {res.error}")


if __name__ == "__main__":
    test_debugger_no_test_files()
    test_debugger_all_pass()
    test_debugger_some_fail()
    test_debugger_failure_structure()
    test_debugger_fallback_old_behavior()
    test_debugger_loop_max_steps()
    print("\n✅ 全部 test_debugger 测试通过\n")
