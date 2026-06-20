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


def join_node(state, ctx):
    return {"log": ["join"]}


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
    "join": join_node,
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


def test_fanout_reducer_merges_dict_updates():
    config = graph_config("g", {
        "reducers": {"items": "fanout"},
        "nodes": {
            "fanout_a": {"fn": "fanout_a"},
            "fanout_b": {"fn": "fanout_b"},
        },
        "edges": [["START", "fanout_a"], ["START", "fanout_b"]],
    })

    def fa(state, ctx):
        return {"items": {ctx.instance_id: "a"}}

    def fb(state, ctx):
        return {"items": {ctx.instance_id: "b"}}

    nodes = dict(NODES)
    nodes["fanout_a"] = fa
    nodes["fanout_b"] = fb
    app = build_graph_from_config(config, "g", nodes, ROUTERS, Checkpointer())
    res = app.invoke({}, thread_id="fanout-reducer")

    assert res.status == "completed"
    assert res.state["items"] == {"fanout_a": "a", "fanout_b": "b"}


def test_json_multi_source_barrier_edge_executes_after_all_sources():
    config = graph_config("g", {
        "reducers": {"log": "append"},
        "nodes": {
            "a": {"fn": "a"},
            "b": {"fn": "b"},
            "join": {"fn": "join"},
        },
        "edges": [
            ["START", "a"],
            ["a", "b"],
            {"from": ["a", "b"], "to": "join"},
            ["join", "END"],
        ],
    })

    app = build_graph_from_config(config, "g", NODES, ROUTERS, Checkpointer())
    res = app.invoke({}, thread_id="json-barrier")

    assert res.status == "completed"
    assert res.state["log"] == ["a", "b", "join"]


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


@pytest.mark.parametrize("max_steps", [0, -1, "0", "abc", None, True])
def test_invalid_max_steps_raises_value_error_with_context(max_steps):
    with pytest.raises(ValueError, match="graph g .*max_steps"):
        build_graph_from_config(
            graph_config("g", {
                "max_steps": max_steps,
                "nodes": {"a": {"fn": "a"}},
                "edges": [["START", "a"]],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


@pytest.mark.parametrize("nodes", ["a", 1, None, True])
def test_nodes_must_be_list_or_object(nodes):
    with pytest.raises(ValueError, match="graph g .*nodes"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": nodes,
                "edges": [["START", "a"]],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


@pytest.mark.parametrize("node_spec", ["a", None, 1, True])
def test_nodes_mapping_specs_must_be_objects(node_spec):
    with pytest.raises(ValueError, match="graph g .*node a .*对象"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": {"a": node_spec},
                "edges": [["START", "a"]],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


def test_canonical_node_mapping_requires_fn():
    with pytest.raises(ValueError, match="graph g .*node a .*fn"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": {"a": {"retries": 0}},
                "edges": [["START", "a"]],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


def test_negative_retries_raise_value_error_with_node_context():
    with pytest.raises(ValueError, match="graph g .*node a .*retries"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": {"a": {"fn": "a", "retries": -1}},
                "edges": [["START", "a"]],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


def test_negative_retry_backoff_raises_value_error_with_node_context():
    with pytest.raises(ValueError, match="graph g .*node a .*retry_backoff"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": {"a": {"fn": "a", "retry_backoff": -0.1}},
                "edges": [["START", "a"]],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


def test_edges_must_be_list():
    with pytest.raises(ValueError, match="graph g .*edges"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": {"a": {"fn": "a"}},
                "edges": {"from": "START", "to": "a"},
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


def test_multi_source_barrier_edge_rejects_unknown_nodes():
    with pytest.raises(ValueError, match="ghost"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": {
                    "a": {"fn": "a"},
                    "join": {"fn": "join"},
                },
                "edges": [
                    ["START", "a"],
                    {"from": ["a", "ghost"], "to": "join"},
                ],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


def test_multi_source_barrier_edge_requires_non_empty_sources():
    with pytest.raises(ValueError, match="至少需要一个源节点"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": {"join": {"fn": "join"}},
                "edges": [{"from": [], "to": "join"}],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


def test_conditional_edges_must_be_list():
    with pytest.raises(ValueError, match="graph g .*conditional_edges"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": {"a": {"fn": "a"}},
                "edges": [["START", "a"]],
                "conditional_edges": {"from": "a", "router": "route_to_c"},
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


@pytest.mark.parametrize("src", ["START", "END"])
def test_conditional_edges_from_cannot_be_start_or_end(src):
    with pytest.raises(ValueError, match="graph g .*conditional_edges.*from"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": {"a": {"fn": "a"}},
                "edges": [["START", "a"]],
                "conditional_edges": [{"from": src, "router": "route_to_c"}],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


def test_unknown_router_error_includes_graph_context():
    with pytest.raises(ValueError, match="graph g .*未知 router.*missing"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": {"a": {"fn": "a"}},
                "edges": [["START", "a"]],
                "conditional_edges": [{"from": "a", "router": "missing"}],
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


def test_conditional_edge_from_undefined_node_raises_value_error():
    with pytest.raises(ValueError, match="conditional_edges.*未定义节点.*ghost"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": {"a": {"fn": "a"}},
                "edges": [["START", "a"]],
                "conditional_edges": [{"from": "ghost", "router": "route_to_c"}],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


def test_conditional_edge_from_handler_name_not_node_name_raises():
    with pytest.raises(ValueError, match="conditional_edges.*未定义节点"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": {"my_node": {"fn": "a"}},
                "edges": [["START", "my_node"]],
                "conditional_edges": [{"from": "a", "router": "route_to_c"}],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )


def test_conditional_edge_from_empty_nodes_list_raises():
    with pytest.raises(ValueError, match="conditional_edges.*未定义节点"):
        build_graph_from_config(
            graph_config("g", {
                "nodes": [],
                "conditional_edges": [{"from": "a", "router": "route_to_c"}],
            }),
            "g", NODES, ROUTERS, Checkpointer(),
        )
