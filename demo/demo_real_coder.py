"""Scenario 6: real coder node writes files to a workdir."""

from agentflow import Checkpointer

from .common import banner, build_configured_graph


def run_real_coder() -> None:
    banner("场景 6 — 真实 Coder：写文件到 workdir")
    import tempfile
    workdir = tempfile.mkdtemp(prefix="af-demo-")
    print(f"\n  workdir: {workdir}")

    cp = Checkpointer()
    app = build_configured_graph("real_coder", cp)

    init = {
        "requirement": "实现 fibonacci 函数, 写单元测试",
        "workdir": workdir,
    }
    res = app.invoke(init, thread_id="real-coder")
    assert res.status == "completed"
    print(f"  artifacts: {res.state.get('artifacts')}")
    # 验证文件实际存在
    import os
    for art in res.state.get("artifacts", []):
        full = os.path.join(workdir, art)
        exists = os.path.isfile(full)
        size = os.path.getsize(full) if exists else 0
        print(f"  {art}: exists={exists}, size={size}")
        if exists:
            with open(full) as f:
                first_line = f.readline().rstrip()
            print(f"    第一行: {first_line}")

    # 清理
    import shutil
    shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    run_real_coder()