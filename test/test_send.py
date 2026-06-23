"""P2-1: dynamic Send/worker execution."""

from collections import Counter
import time

from agentflow import (
    Checkpointer,
    Command,
    END,
    Send,
    START,
    StateGraph,
    StateSchema,
    append_reducer,
    fanout_reducer,
)


calls = Counter()


def start_node(state, ctx):
    calls["start"] += 1
    return {"items": [1, 2], "log": ["start"]}


def route_sends(state):
    return [
        Send("worker", {"item": item}, key=str(item))
        for item in state.get("items", [])
    ]


def worker_node(state, ctx):
    calls[ctx.instance_id] += 1
    return {
        "seen": [state["item"]],
        "fanout": {ctx.instance_id: state["item"] * 10},
        "log": [f"worker:{state['item']}"],
    }


def join_node(state, ctx):
    calls["join"] += 1
    return {"log": [f"join:{sorted(state['fanout'].values())}"]}


def build_send_graph():
    schema = StateSchema(reducers={
        "log": append_reducer,
        "seen": append_reducer,
        "fanout": fanout_reducer,
    })
    g = StateGraph(schema)
    g.add_node("start", start_node)
    g.add_node("worker", worker_node)
    g.add_node("join", join_node)
    g.add_edge(START, "start")
    g.add_conditional_edges("start", route_sends)
    g.add_edge("worker", "join")
    g.add_edge("join", END)
    return g


def test_router_returns_multiple_send_instances():
    calls.clear()
    app = build_send_graph().compile(Checkpointer())

    res = app.invoke({}, thread_id="send-basic")

    assert res.status == "completed"
    assert sorted(res.state["seen"]) == [1, 2]
    assert sorted(res.state["fanout"].values()) == [10, 20]
    assert len([k for k in calls if k.startswith("worker:")]) == 2


def test_send_arg_is_not_written_to_global_state():
    app = build_send_graph().compile(Checkpointer())

    res = app.invoke({}, thread_id="send-arg")

    assert res.status == "completed"
    assert "item" not in res.state


def test_same_worker_activity_cache_is_instance_scoped():
    counter = Counter()

    def start(state, ctx):
        return {"items": [1, 2]}

    def route(state):
        return [Send("worker", {"item": item}, key=str(item)) for item in state["items"]]

    def worker(state, ctx):
        value = ctx.activity("expensive", lambda: state["item"] * 100)
        counter[ctx.instance_id] += 1
        return {"fanout": {ctx.instance_id: value}}

    g = StateGraph(StateSchema(reducers={"fanout": fanout_reducer}))
    g.add_node("start", start)
    g.add_node("worker", worker)
    g.add_edge(START, "start")
    g.add_conditional_edges("start", route)

    app = g.compile(Checkpointer())
    res = app.invoke({}, thread_id="send-cache")

    assert res.status == "completed"
    assert sorted(res.state["fanout"].values()) == [100, 200]
    assert sum(counter.values()) == 2


def test_send_target_must_exist():
    def start(state, ctx):
        return {}

    def route(state):
        return Send("missing", {"x": 1})

    g = StateGraph()
    g.add_node("start", start)
    g.add_edge(START, "start")
    g.add_conditional_edges("start", route)

    res = g.compile(Checkpointer()).invoke({}, thread_id="send-missing")

    assert res.status == "failed"
    assert "missing" in res.error


def test_empty_send_list_terminates_path():
    def start(state, ctx):
        return {"done": True}

    def route(state):
        return []

    g = StateGraph()
    g.add_node("start", start)
    g.add_edge(START, "start")
    g.add_conditional_edges("start", route)

    res = g.compile(Checkpointer()).invoke({}, thread_id="send-empty")

    assert res.status == "completed"
    assert res.state["done"] is True


def test_mixed_send_and_static_node_keep_batch_order():
    def start(state, ctx):
        return {"log": ["start"]}

    def route(state):
        return [Send("worker", {"item": "send"}, key="send"), "plain"]

    def worker(state, ctx):
        return {"log": [f"worker:{state['item']}"]}

    def plain(state, ctx):
        return {"log": ["plain"]}

    g = StateGraph(StateSchema(reducers={"log": append_reducer}))
    g.add_node("start", start)
    g.add_node("worker", worker)
    g.add_node("plain", plain)
    g.add_edge(START, "start")
    g.add_conditional_edges("start", route)

    res = g.compile(Checkpointer()).invoke({}, thread_id="send-mixed")

    assert res.status == "completed"
    assert res.state["log"] == ["start", "worker:send", "plain"]


def test_checkpoint_resume_keeps_send_instance_id_stable():
    seen_instances = []

    def start(state, ctx):
        return {"items": [1]}

    def route(state):
        return [Send("worker", {"item": item}, key=str(item)) for item in state["items"]]

    def worker(state, ctx):
        seen_instances.append(ctx.instance_id)
        decision = ctx.interrupt({"item": state["item"], "instance": ctx.instance_id})
        return {"seen": [decision]}

    g = StateGraph(StateSchema(reducers={"seen": append_reducer}))
    g.add_node("start", start)
    g.add_node("worker", worker)
    g.add_edge(START, "start")
    g.add_conditional_edges("start", route)
    cp = Checkpointer()
    app = g.compile(cp)

    r1 = app.invoke({}, thread_id="send-resume")
    assert r1.status == "interrupted"
    frontier = cp.latest("send-resume").frontier
    worker_items = [item for item in frontier if item["kind"] == "node" and item["node"] == "worker"]
    assert len(worker_items) == 1
    first_instance = worker_items[0]["instance_id"]

    r2 = app.invoke({}, thread_id="send-resume", command=Command(resume="ok"))
    assert r2.status == "completed"
    assert seen_instances[0] == first_instance
    assert first_instance in seen_instances[1:]
    assert r2.state["seen"] == ["ok"]


def test_send_interrupt_commits_completed_sibling_without_rerun():
    counter = Counter()

    def start(state, ctx):
        return {"items": ["slow", "fast"]}

    def route(state):
        return [
            Send("worker", {"item": "slow"}, key="slow"),
            Send("worker", {"item": "fast"}, key="fast"),
        ]

    def worker(state, ctx):
        item = state["item"]
        counter[item] += 1
        if item == "slow":
            time.sleep(0.2)
            decision = ctx.interrupt({"item": item, "calls": dict(counter)})
            return {"seen": ["slow:" + decision]}
        return {"seen": ["fast"]}

    g = StateGraph(StateSchema(reducers={"seen": append_reducer}))
    g.add_node("start", start)
    g.add_node("worker", worker)
    g.add_edge(START, "start")
    g.add_conditional_edges("start", route)
    cp = Checkpointer()
    app = g.compile(cp)

    r1 = app.invoke({}, thread_id="send-interrupt-sibling")

    assert r1.status == "interrupted"
    assert counter["fast"] == 1
    assert counter["slow"] == 1
    assert r1.state["seen"] == ["fast"]

    checkpoint = cp.latest("send-interrupt-sibling")
    worker_items = [
        item for item in checkpoint.frontier
        if item["kind"] == "node" and item["node"] == "worker"
    ]
    assert len(worker_items) == 1
    assert worker_items[0]["arg"] == {"item": "slow"}

    r2 = app.invoke({}, thread_id="send-interrupt-sibling", command=Command(resume="ok"))

    assert r2.status == "completed"
    assert counter["fast"] == 1
    assert counter["slow"] == 2
    assert r2.state["seen"] == ["fast", "slow:ok"]


def test_strict_barrier_waits_for_all_sources():
    def a(state, ctx):
        return {"log": ["a"]}

    def route_a(state):
        return "b"

    def b(state, ctx):
        return {"log": ["b"]}

    def join(state, ctx):
        return {"log": ["join"]}

    g = StateGraph(StateSchema(reducers={"log": append_reducer}))
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_node("join", join)
    g.add_edge(START, "a")
    g.add_conditional_edges("a", route_a)
    g.add_edge(["a", "b"], "join")

    res = g.compile(Checkpointer()).invoke({}, thread_id="barrier")

    assert res.status == "completed"
    assert res.state["log"] == ["a", "b", "join"]


def test_fanout_reducer_requires_dict():
    try:
        fanout_reducer({}, ["bad"])
        assert False, "fanout_reducer should reject non-dict updates"
    except TypeError:
        pass


ALL_TESTS = [
    test_router_returns_multiple_send_instances,
    test_send_arg_is_not_written_to_global_state,
    test_same_worker_activity_cache_is_instance_scoped,
    test_send_target_must_exist,
    test_empty_send_list_terminates_path,
    test_mixed_send_and_static_node_keep_batch_order,
    test_checkpoint_resume_keeps_send_instance_id_stable,
    test_send_interrupt_commits_completed_sibling_without_rerun,
    test_strict_barrier_waits_for_all_sources,
    test_fanout_reducer_requires_dict,
]


if __name__ == "__main__":
    import sys
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
    print(f"\n✅ 全部 {len(ALL_TESTS)} 个 Send 测试通过\n")