from __future__ import annotations

import json

import pytest

from agentflow import (
    Checkpointer,
    END,
    build_graph_from_config,
    build_state_graph_from_config,
    load_graph_config,
)


def node_a(state, ctx):
    return {"seen": "a", "log": ["a"]}


def node_b(state, ctx):
    return {"seen": "b", "log": ["b"]}


def node_c(state, ctx):
    return {"done": True, "log": ["c"]}


def fanout_a(state, ctx):
    return {"items": ["a"]}


def fanout_b(state, ctx):
    return {"items": ["b"]}


def flaky(state, ctx):
    if ctx.attempt < 2:
        raise RuntimeError("retry me")
    return {"attempt": ctx.attempt, "log": ["ok"]}


def route_to_c(state):
    return "c"


def route_to_end_string(state):
    return "END"


NODES = {
    "a": node_a,
    "b": node_b,
    "c": node_c,
    "fanout_a": fanout_a,
    "fanout_b": fanout_b,
    "flaky": flaky,
}

ROUTERS = {
    "route_to_c": route_to_c,
    "route_to_end_string": route_to_end_string,
}


def graph_config(name, spec):
    return {"graphs": {name: spec}}


def test_load_graph_config_reads_json(tmp_path):
    path = tmp_path / "graph.json"
    data = graph_config("g", {"nodes": ["a"], "edges": [["START", "a"]]})
    path.write_text(json.dumps(data), encoding="utf-8")

    assert load_graph_config(str(path)) == data


def test_minimal_graph_executes_with_start_end_aliases():
    config = graph_config("g", {
        "nodes": ["a"],
        "edges": [["START", "a"], ["a", "END"]],
    })

    app = build_graph_from_config(config, "g", NODES, ROUTERS, Checkpointer())
    res = app.invoke({}, thread_id="minimal")

    assert res.status == "completed"
    assert res.state["seen"] == "a"


def test_nodes_mapping_with_fn_field_executes():
    config = graph_config("g", {
        "nodes": {
            "worker": {"fn": "flaky", "retries": 2},
        },
        "edges": [["START", "worker"], ["worker", "END"]],
    })

    app = build_graph_from_config(config, "g", NODES, ROUTERS, Checkpointer())
    res = app.invoke({}, thread_id="mapping-fn")

    assert res.status == "completed"
    assert res.state["attempt"] == 2


def test_node_object_fn_alias_executes():
    config = graph_config("g", {
        "nodes": [{"name": "worker", "fn": "a"}],
        "edges": [["START", "worker"], ["worker", "END"]],
    })

    app = build_graph_from_config(config, "g", NODES, ROUTERS, Checkpointer())
    res = app.invoke({}, thread_id="object-fn")

    assert res.status == "completed"
    assert res.state["seen"] == "a"


def test_append_reducer_merges_parallel_updates_in_batch_order():
    config = graph_config("g", {
        "reducers": {"items": "append"},
        "nodes": ["fanout_a", "fanout_b"],
        "edges": [["START", "fanout_a"], ["START", "fanout_b"]],
    })

    app = build_graph_from_config(config, "g", NODES, ROUTERS, Checkpointer())
    res = app.invoke({}, thread_id="append")

    assert res.status == "completed"
    assert res.state["items"] == ["a", "b"]


def test_conditional_edge_uses_registry_router():
    config = graph_config("g", {
        "nodes": ["a", "c"],
        "edges": [["START", "a"]],
        "conditional_edges": [
            {"from": "a", "router": "route_to_c"},
            {"from": "c", "router": "route_to_end_string"},
        ],
    })

    app = build_graph_from_config(config, "g", NODES, ROUTERS, Checkpointer())
    res = app.invoke({}, thread_id="cond")

    assert res.status == "completed"
    assert res.state["done"] is True


def test_retries_from_node_config():
    config = graph_config("g", {
        "reducers": {"log": "append"},
        "nodes": [{"name": "flaky", "handler": "flaky", "retries": 2}],
        "edges": [["START", "flaky"]],
    })

    app = build_graph_from_config(config, "g", NODES, ROUTERS, Checkpointer())
    res = app.invoke({}, thread_id="retry")

    assert res.status == "completed"
    assert res.state["attempt"] == 2
    assert res.state["log"] == ["ok"]


def test_unknown_node_router_and_reducer_raise_value_error():
    with pytest.raises(ValueError, match="未知 node"):
        build_graph_from_config(
            graph_config("g", {"nodes": ["missing"], "edges": [["START", "missing"]]}),
            "g", NODES, ROUTERS, Checkpointer(),
        )

    with pytest.raises(ValueError, match="未知 router"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": ["a"],
                "edges": [["START", "a"]],
                "conditional_edges": [{"from": "a", "router": "missing"}],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )

    with pytest.raises(ValueError, match="未知 reducer"):
        build_graph_from_config(
            graph_config("g", {
                "reducers": {"log": "extend"},
                "nodes": ["a"],
                "edges": [["START", "a"]],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


def test_validate_can_be_used_on_json_built_state_graph():
    config = graph_config("g", {
        "nodes": ["a", "b"],
        "edges": [["START", "a"], ["b", END]],
    })

    g = build_state_graph_from_config(config, "g", NODES, ROUTERS)
    issues = g.validate()

    assert any(i.level == "error" and i.node == "b" for i in issues)
