"""Scenario 4: checkpoint history / time travel output."""
from __future__ import annotations

from agentflow import Checkpointer

from .common import banner, build_configured_graph


def run_timetravel() -> None:
    banner("场景 4 — 时间旅行：checkpoint 历史 + 事件日志")
    cp = Checkpointer()
    app = build_configured_graph("timetravel", cp)
    tid = "tt"
    app.invoke({"requirement": "做个 CLI 工具", "pass_at_version": 2}, thread_id=tid)
    print("\n  checkpoint 历史(每个 super-step 一份):")
    for c in cp.history(tid):
        print(f"    step={c.step:>2} status={c.status:<11} "
              f"frontier={c.frontier} code_version={c.state.get('code_version')}")


if __name__ == "__main__":
    run_timetravel()
