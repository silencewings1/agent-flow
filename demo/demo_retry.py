"""Scenario 3: node retry and event log output."""

from agentflow import Checkpointer

from .common import banner, build_configured_graph


def run_retry() -> None:
    banner("场景 3 — 节点错误重试")
    cp = Checkpointer()
    app = build_configured_graph("retry", cp)
    res = app.invoke({}, thread_id="retry")
    print(f"\n→ status={res.status}")
    for line in res.state["log"]:
        print(f"    {line}")
    print("  事件日志(可见 node_retry):")
    for e in cp.events("retry"):
        if e["kind"] in ("node_retry", "node_ok"):
            print(f"    seq={e['seq']} {e['kind']} {e['payload']}")


if __name__ == "__main__":
    run_retry()