"""Build StateGraph instances from declarative JSON config.

The config layer is intentionally small and closed-world: node and router names
must be provided by explicit registries from the caller. It never imports or
evaluates code from JSON.
"""
from __future__ import annotations

import json
import math
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .checkpoint import Checkpointer
from .graph import END, START, CompiledGraph, RouterFn, Send, StateGraph, ValidationIssue
from .state import StateSchema, append_reducer, fanout_reducer, overwrite_reducer


NodeRegistry = Mapping[str, Callable[..., Any]]
RouterRegistry = Mapping[str, RouterFn]

_REDUCERS = {
    "append": append_reducer,
    "fanout": fanout_reducer,
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
    - edges: [["START", "node"], {"from": "node", "to": "END"},
      {"from": ["a", "b"], "to": "join"}, ...]
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

    schema = StateSchema(reducers=_parse_reducers(spec.get("reducers", {}), graph_name))
    max_steps = _parse_positive_int(spec.get("max_steps", 50), graph_name, "max_steps")
    g = StateGraph(schema, max_steps=max_steps)

    declared_nodes: set = set()
    for node_spec in _iter_node_specs(spec.get("nodes", []), graph_name):
        node = _parse_node(node_spec, graph_name)
        handler_name = node["handler"]
        if handler_name not in node_registry:
            raise ValueError(f"graph {graph_name} 未知 node: {handler_name}")
        declared_nodes.add(node["name"])
        g.add_node(
            node["name"],
            node_registry[handler_name],
            retries=_parse_non_negative_int(
                node.get("retries", 0), graph_name, node["name"], "retries"
            ),
            retry_backoff=_parse_non_negative_float(
                node.get("retry_backoff", 0.0),
                graph_name,
                node["name"],
                "retry_backoff",
            ),
        )

    for index, edge_spec in enumerate(_parse_list_field(spec, graph_name, "edges")):
        src, dst = _parse_edge(edge_spec, graph_name, index)
        if isinstance(src, list):
            g.add_edge([_alias_node(s) for s in src], _alias_node(dst))
        else:
            g.add_edge(_alias_node(src), _alias_node(dst))

    for index, cond_spec in enumerate(
        _parse_list_field(spec, graph_name, "conditional_edges")
    ):
        cond = _parse_conditional_edge(cond_spec, graph_name, index)
        cond_src = cond["from"]
        if cond_src not in declared_nodes:
            raise ValueError(
                f"graph {graph_name} conditional_edges[{index}] "
                f"引用了未定义节点: {cond_src}"
            )
        router_name = cond["router"]
        if router_name not in router_registry:
            raise ValueError(f"graph {graph_name} 未知 router: {router_name}")
        g.add_conditional_edges(
            _alias_node(cond_src),
            _wrap_router_aliases(router_registry[router_name]),
        )

    return g


def _parse_reducers(raw: Any, graph_name: str) -> Dict[str, Callable[[Any, Any], Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"graph {graph_name} reducers 必须是对象")
    reducers = {}
    for key, reducer_name in raw.items():
        if reducer_name not in _REDUCERS:
            raise ValueError(f"graph {graph_name} 未知 reducer: {reducer_name}")
        reducers[str(key)] = _REDUCERS[reducer_name]
    return reducers


def _iter_node_specs(raw: Any, graph_name: str) -> List[Any]:
    if isinstance(raw, Mapping):
        specs = []
        for name, node_spec in raw.items():
            if not isinstance(node_spec, Mapping):
                raise ValueError(
                    f"graph {graph_name} node {name} spec 必须是对象"
                )
            spec = dict(node_spec)
            if "fn" not in spec:
                raise ValueError(
                    f"graph {graph_name} node {name} 必须显式配置 fn"
                )
            spec.setdefault("name", name)
            specs.append(spec)
        return specs
    if isinstance(raw, list):
        return raw
    raise ValueError(f"graph {graph_name} nodes 必须是数组或对象")


def _parse_node(raw: Any, graph_name: str) -> Dict[str, Any]:
    if isinstance(raw, str):
        return {"name": raw, "handler": raw}
    if not isinstance(raw, Mapping):
        raise ValueError(f"graph {graph_name} nodes 项必须是字符串或对象")
    name = raw.get("name")
    handler = raw.get("handler", raw.get("fn"))
    if not name or not handler:
        raise ValueError(
            f"graph {graph_name} node 必须包含 name，且 handler/fn 不能为空"
        )
    return {
        "name": str(name),
        "handler": str(handler),
        "retries": raw.get("retries", 0),
        "retry_backoff": raw.get("retry_backoff", 0.0),
    }


def _parse_edge(raw: Any, graph_name: str, index: int) -> List[Any]:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return [str(raw[0]), str(raw[1])]
    if isinstance(raw, Mapping) and "from" in raw and "to" in raw:
        src = raw["from"]
        if isinstance(src, list):
            if not src:
                raise ValueError(
                    f"graph {graph_name} edges[{index}].from 至少需要一个源节点"
                )
            return [[str(s) for s in src], str(raw["to"])]
        return [str(src), str(raw["to"])]
    raise ValueError(
        f"graph {graph_name} edges[{index}] 必须是 [from, to] 或 {{from, to}}"
    )


def _parse_conditional_edge(raw: Any, graph_name: str, index: int) -> Dict[str, str]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"graph {graph_name} conditional_edges[{index}] 必须是对象")
    src = raw.get("from")
    router = raw.get("router")
    if not src or not router:
        raise ValueError(
            f"graph {graph_name} conditional_edges[{index}] 必须包含 from 和 router"
        )
    src_name = str(src)
    if _alias_node(src_name) in (START, END):
        raise ValueError(
            f"graph {graph_name} conditional_edges[{index}].from 不能是 START 或 END"
        )
    return {"from": src_name, "router": str(router)}


def _parse_list_field(spec: Mapping[str, Any], graph_name: str, field: str) -> List[Any]:
    raw = spec.get(field, [])
    if not isinstance(raw, list):
        raise ValueError(f"graph {graph_name} {field} 必须是数组")
    return raw


def _parse_positive_int(raw: Any, graph_name: str, field: str) -> int:
    value = _parse_int(raw, f"graph {graph_name} {field} 必须是 > 0 的整数")
    if value <= 0:
        raise ValueError(f"graph {graph_name} {field} 必须是 > 0 的整数")
    return value


def _parse_non_negative_int(raw: Any, graph_name: str, node_name: str, field: str) -> int:
    value = _parse_int(
        raw, f"graph {graph_name} node {node_name} {field} 必须是 >= 0 的整数"
    )
    if value < 0:
        raise ValueError(
            f"graph {graph_name} node {node_name} {field} 必须是 >= 0 的整数"
        )
    return value


def _parse_non_negative_float(
    raw: Any, graph_name: str, node_name: str, field: str
) -> float:
    if isinstance(raw, bool):
        raise ValueError(
            f"graph {graph_name} node {node_name} {field} 必须是 >= 0 的数字"
        )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"graph {graph_name} node {node_name} {field} 必须是 >= 0 的数字"
        )
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(
            f"graph {graph_name} node {node_name} {field} 必须是 >= 0 的数字"
        )
    return value


def _parse_int(raw: Any, error_message: str) -> int:
    if isinstance(raw, bool):
        raise ValueError(error_message)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError(error_message)
        try:
            return int(text)
        except ValueError:
            raise ValueError(error_message)
    raise ValueError(error_message)


def _alias_node(name: str) -> str:
    if name in ("START", START):
        return START
    if name in ("END", END):
        return END
    return name


def _wrap_router_aliases(router: RouterFn) -> RouterFn:
    def alias_out(value: Any) -> Any:
        if isinstance(value, Send):
            return Send(_alias_node(value.node), value.arg, value.key)
        if isinstance(value, str):
            return _alias_node(value)
        return value

    def wrapped(state: Dict[str, Any]) -> Any:
        out = router(state)
        if isinstance(out, (list, tuple)):
            return [alias_out(x) for x in out]
        return alias_out(out)

    wrapped.__name__ = getattr(router, "__name__", "router")
    return wrapped


def _format_validation_errors(errors: Iterable[ValidationIssue]) -> str:
    return "graph config validation failed: " + "; ".join(str(e) for e in errors)
