"""Scenario 2: static fan-out with a strict barrier join."""
from __future__ import annotations

from agentflow import Checkpointer

from .common import banner, build_configured_graph


def run_parallel() -> None:
    banner("场景 2 — 并行扇出：同一 super-step 内多节点并发 + barrier 汇聚")
    cp = Checkpointer()
    app = build_configured_graph("parallel", cp)
    res = app.invoke({"artifacts": []}, thread_id="fanout")
    print(f"\n→ status={res.status}, step={res.step}（split/3并行/join = 3 个 super-step）")
    for line in res.state["log"]:
        print(f"    {line}")


if __name__ == "__main__":
    run_parallel()
