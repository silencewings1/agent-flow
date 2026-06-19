"""Build StateGraph instances from declarative JSON config.

The config layer is intentionally small and closed-world: node and router names
must be provided by explicit registries from the caller. It never imports or
evaluates code from JSON.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .checkpoint import Checkpointer
from .graph import END, START, CompiledGraph, RouterFn, StateGraph, ValidationIssue
from .state import StateSchema, append_reducer, overwrite_reducer


NodeRegistry = Mapping[str, Callable[..., Any]]
RouterRegistry = Mapping[str, RouterFn]

_REDUCERS = {
    "append": append_reducer,
    "overwrite": overwrite_reducer,
}


def load_graph_config(path: str) -> Dict[str, Any]:
    """Read a JSON graph config file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_graph_from_config(
    config: Mapping[str, Any],
    graph_name: str,
    node_registry: NodeRegistry,
    router_registry: RouterRegistry,
    checkpointer: Optional[Checkpointer] = None,
) -> CompiledGraph:
    """Build and compile a graph named ``graph_name`` from config.

    Supported fields under ``graphs.<graph_name>``:
    - max_steps: int
    - reducers: {"state_key": "append"|"overwrite"}
    - nodes: {"node": {"fn": "handler", "retries": 0, ...}, ...}
      or [{"name": "...", "handler": "...", "retries": 0, ...}, ...].
      "fn" is an alias for "handler"; a plain string node is shorthand for
      {"name": value, "handler": value}.
    - edges: [["START", "node"], {"from": "node", "to": "END"}, ...]
    - conditional_edges: [{"from": "node", "router": "route_name"}, ...]
    """
    g = build_state_graph_from_config(config, graph_name, node_registry, router_registry)
    issues = g.validate()
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise ValueError(_format_validation_errors(errors))
    return g.compile(checkpointer)


def build_state_graph_from_config(
    config: Mapping[str, Any],
    graph_name: str,
    node_registry: NodeRegistry,
    router_registry: RouterRegistry,
) -> StateGraph:
    """Build an uncompiled StateGraph from JSON config.

    This helper lets callers run ``validate()`` or ``to_mermaid()`` before
    compiling. ``build_graph_from_config`` is the main convenience API.
    """
    graphs = config.get("graphs")
    if not isinstance(graphs, Mapping) or graph_name not in graphs:
        raise ValueError(f"未知 graph: {graph_name}")
    spec = graphs[graph_name]
    if not isinstance(spec, Mapping):
        raise ValueError(f"graph {graph_name} 必须是对象")

    schema = StateSchema(reducers=_parse_reducers(spec.get("reducers", {})))
    max_steps = int(spec.get("max_steps", 50))
    g = StateGraph(schema, max_steps=max_steps)

    for node_spec in _iter_node_specs(spec.get("nodes", [])):
        node = _parse_node(node_spec)
        handler_name = node["handler"]
        if handler_name not in node_registry:
            raise ValueError(f"未知 node: {handler_name}")
        g.add_node(
            node["name"],
            node_registry[handler_name],
            retries=int(node.get("retries", 0)),
            retry_backoff=float(node.get("retry_backoff", 0.0)),
        )

    for edge_spec in spec.get("edges", []):
        src, dst = _parse_edge(edge_spec)
        g.add_edge(_alias_node(src), _alias_node(dst))

    for cond_spec in spec.get("conditional_edges", []):
        cond = _parse_conditional_edge(cond_spec)
        router_name = cond["router"]
        if router_name not in router_registry:
            raise ValueError(f"未知 router: {router_name}")
        g.add_conditional_edges(
            _alias_node(cond["from"]),
            _wrap_router_aliases(router_registry[router_name]),
        )

    return g


def _parse_reducers(raw: Any) -> Dict[str, Callable[[Any, Any], Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("reducers 必须是对象")
    reducers = {}
    for key, reducer_name in raw.items():
        if reducer_name not in _REDUCERS:
            raise ValueError(f"未知 reducer: {reducer_name}")
        reducers[str(key)] = _REDUCERS[reducer_name]
    return reducers


def _iter_node_specs(raw: Any) -> List[Any]:
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        specs = []
        for name, node_spec in raw.items():
            if isinstance(node_spec, Mapping):
                spec = dict(node_spec)
                spec.setdefault("name", name)
            elif isinstance(node_spec, str):
                spec = {"name": name, "handler": node_spec}
            elif node_spec is None:
                spec = {"name": name, "handler": name}
            else:
                raise ValueError("nodes 对象的值必须是对象、字符串或 null")
            specs.append(spec)
        return specs
    if isinstance(raw, list):
        return raw
    raise ValueError("nodes 必须是数组或对象")


def _parse_node(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        return {"name": raw, "handler": raw}
    if not isinstance(raw, Mapping):
        raise ValueError("nodes 项必须是字符串或对象")
    name = raw.get("name")
    handler = raw.get("handler", raw.get("fn", name))
    if not name or not handler:
        raise ValueError("node 必须包含 name，且 handler 不能为空")
    return {
        "name": str(name),
        "handler": str(handler),
        "retries": raw.get("retries", 0),
        "retry_backoff": raw.get("retry_backoff", 0.0),
    }


def _parse_edge(raw: Any) -> List[str]:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return [str(raw[0]), str(raw[1])]
    if isinstance(raw, Mapping) and "from" in raw and "to" in raw:
        return [str(raw["from"]), str(raw["to"])]
    raise ValueError("edges 项必须是 [from, to] 或 {from, to}")


def _parse_conditional_edge(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, Mapping):
        raise ValueError("conditional_edges 项必须是对象")
    src = raw.get("from")
    router = raw.get("router")
    if not src or not router:
        raise ValueError("conditional_edges 项必须包含 from 和 router")
    return {"from": str(src), "router": str(router)}


def _alias_node(name: str) -> str:
    if name in ("START", START):
        return START
    if name in ("END", END):
        return END
    return name


def _wrap_router_aliases(router: RouterFn) -> RouterFn:
    def wrapped(state: Dict[str, Any]) -> Any:
        out = router(state)
        if isinstance(out, (list, tuple)):
            return [_alias_node(str(x)) if isinstance(x, str) else x for x in out]
        if isinstance(out, str):
            return _alias_node(out)
        return out

    wrapped.__name__ = getattr(router, "__name__", "router")
    return wrapped


def _format_validation_errors(errors: Iterable[ValidationIssue]) -> str:
    return "graph config validation failed: " + "; ".join(str(e) for e in errors)
