"""Scenario 7: real debugger pytest loop with a fixing coder."""
from __future__ import annotations

from agentflow import Checkpointer

from .common import banner, build_configured_graph


def run_real_debugger() -> None:
    banner("场景 7 — 真实 Debugger：pytest 回环")
    import os
    import tempfile
    workdir = tempfile.mkdtemp(prefix="af-demo-dbg-")
    print(f"\n  workdir: {workdir}")

    # 先写一个会失败的测试文件
    src_dir = os.path.join(workdir, "src")
    os.makedirs(src_dir, exist_ok=True)
    # 必须有 __init__.py 才能做 `from src.task_t1 import ...`
    with open(os.path.join(src_dir, "__init__.py"), "w") as f:
        f.write("# auto-generated package\n")
    with open(os.path.join(src_dir, "task_t1.py"), "w") as f:
        f.write("def fib(n):\n"
                "    if n <= 1: return n\n"
                "    return fib(n-1) + fib(n-2)\n")
    with open(os.path.join(src_dir, "test_fib.py"), "w") as f:
        f.write("from src.task_t1 import fib\n"
                "def test_fib_0():\n    assert fib(0) == 0\n"
                "def test_fib_5():\n    assert fib(5) == 99  # 故意写错\n")

    cp = Checkpointer()
    app = build_configured_graph("real_debugger", cp)

    init = {
        "tasks": ["t1"],
        "code_version": 1,
        "workdir": workdir,
        "artifacts": ["src/task_t1.py"],
    }
    res = app.invoke(init, thread_id="real-dbg")
    # 第一次：测试失败 → 退回 coder
    assert res.status in ("completed", "failed"), f"unexpected status: {res.status}"
    if res.status == "completed":
        print(f"  状态: {res.status}")
        print(f"  tests_passed: {res.state.get('tests_passed')}")
        print(f"  test_failures: {res.state.get('test_failures')}")
    elif res.status == "failed":
        print(f"  状态: failed (超过 max_steps，可能是正确的回环)")
    for line in res.state.get("log", []):
        print(f"    {line}")

    import shutil
    shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    run_real_debugger()
