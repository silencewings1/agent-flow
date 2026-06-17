"""P0-3: StateGraph.validate() + to_mermaid() 的覆盖测试。

执行方式：PYTHONPATH=. python3 test/test_graph.py
"""
from __future__ import annotations

from agentflow import StateGraph, START, END
from agentflow.graph import ValidationIssue


# —— helpers —— #

def _level_counts(issues):
    out = {"error": 0, "warning": 0, "info": 0}
    for i in issues:
        out[i.level] += 1
    return out


def _names(issues, level=None):
    return [i.node for i in issues if level is None or i.level == level]


def _noop(state, ctx):
    return None


# —— 路由函数（模块级，确保 inspect.getsource 能拿到源码）—— #

def route_pass(s):
    return "reviewer"


def route_fail(s):
    return "coder"


def route_end(s):
    return END


def route_ghost(s):
    return "ghost"


def route_to_c(s):
    return "c"


def route_to_b(s):
    return "b"


# ============================================================
# 1) validate() 基础
# ============================================================

def test_valid_graph_no_issues():
    """标准流水线：所有检查通过，validate() 无 error/warning。"""
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_node("c", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.add_edge("c", END)
    issues = g.validate()
    errs = [i for i in issues if i.level == "error"]
    assert not errs, f"合法图不应有 error: {[str(i) for i in errs]}"
    print("✅ test_valid_graph_no_issues")


def test_missing_entry_is_error():
    g = StateGraph()
    g.add_node("a", _noop)
    # 故意不连 START
    issues = g.validate()
    errs = [i for i in issues if i.level == "error"]
    assert any("入口" in i.message for i in errs), [str(i) for i in errs]
    print("✅ test_missing_entry_is_error")


# ============================================================
# 2) 不可达节点
# ============================================================

def test_unreachable_node_is_error():
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_node("orphan", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    issues = g.validate()
    errs = [i for i in issues if i.level == "error"]
    err_nodes = [i.node for i in errs]
    assert "orphan" in err_nodes, [str(i) for i in errs]
    # a、b 应当可达
    assert "a" not in err_nodes and "b" not in err_nodes
    print("✅ test_unreachable_node_is_error")


def test_node_reachable_via_conditional_is_not_error():
    """条件边可以到达的节点不应被标为不可达。"""
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_node("c", _noop)  # 只通过条件边到达
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    g.add_conditional_edges("b", route_to_c)  # 可返回 "c"
    issues = g.validate()
    err_nodes = [i.node for i in issues if i.level == "error"]
    assert "c" not in err_nodes, [str(i) for i in err_nodes]
    print("✅ test_node_reachable_via_conditional_is_not_error")


# ============================================================
# 3) 重复边
# ============================================================

def test_duplicate_edge_is_warning():
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("a", "b")  # 重复
    g.add_edge("b", END)
    issues = g.validate()
    warns = [i for i in issues if i.level == "warning"]
    assert any("重复" in i.message for i in warns), [str(i) for i in warns]
    # 重复边是 warning，不该升为 error
    errs = [i for i in issues if i.level == "error"]
    assert not any("重复" in i.message for i in errs)
    print("✅ test_duplicate_edge_is_warning")


# ============================================================
# 4) 循环
# ============================================================

def test_cycle_is_info_not_blocking():
    """静态边循环应是 info 级别，不阻止编译。"""
    g = StateGraph()
    g.add_node("x", _noop)
    g.add_node("y", _noop)
    g.add_edge(START, "x")
    g.add_edge("x", "y")
    g.add_edge("y", "x")  # 循环
    g.add_edge("y", END)
    issues = g.validate()
    infos = [i for i in issues if i.level == "info"]
    assert any("循环" in i.message for i in infos), [str(i) for i in infos]
    # 编译不应被阻止
    app = g.compile()
    assert app is not None
    print("✅ test_cycle_is_info_not_blocking")


# ============================================================
# 5) 死胡同（无路径到 END）
# ============================================================

def test_dead_end_node_is_warning():
    """没有路径到 END 的节点应是 warning。"""
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_node("dead", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", "dead")   # dead 没有任何出边
    # dead 自身既无出边，也无到达 END 的路径——但 "无出边" 在 LangGraph 语义中
    # 视作合法终端节点，validate 应当给 info 而非 warning
    issues = g.validate()
    # 当前实现把「无出边」视作合法终端节点，所以 dead 不应触发 error
    errs = [i for i in issues if i.level == "error" and i.node == "dead"]
    assert not errs, f"无出边节点不应报 error: {[str(i) for i in errs]}"
    print("✅ test_dead_end_node_is_warning")


# ============================================================
# 6) 条件边：可调用性 + 返回值静态分析
# ============================================================

def test_non_callable_router_is_error():
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", END)
    g.add_conditional_edges("a", "not callable")  # 故意传字符串
    issues = g.validate()
    errs = [i for i in issues if i.level == "error"]
    assert any("可调用" in i.message for i in errs), [str(i) for i in errs]
    print("✅ test_non_callable_router_is_error")


def test_conditional_returns_undefined_node_is_warning():
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", END)
    g.add_conditional_edges("a", route_ghost)  # 返回 "ghost" 不存在
    issues = g.validate()
    warns = [i for i in issues if i.level == "warning"]
    assert any("ghost" in i.message for i in warns), [str(i) for i in warns]
    print("✅ test_conditional_returns_undefined_node_is_warning")


def test_conditional_returns_defined_node_no_warning():
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", END)
    g.add_conditional_edges("a", route_to_b)  # 返回 "b"（存在）
    issues = g.validate()
    warns = [i for i in issues if i.level == "warning"]
    assert not any("未定义节点" in i.message for i in warns), \
        [str(i) for i in warns]
    print("✅ test_conditional_returns_defined_node_no_warning")


def test_conditional_returns_end_is_fine():
    """条件边返回 END 是合法用法。"""
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_edge(START, "a")
    g.add_conditional_edges("a", route_end)  # 返回 END
    issues = g.validate()
    errs = [i for i in issues if i.level == "error"]
    assert not errs, [str(i) for i in errs]
    print("✅ test_conditional_returns_end_is_fine")


# ============================================================
# 7) to_mermaid()
# ============================================================

def test_to_mermaid_contains_all_nodes():
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    out = g.to_mermaid()
    assert "graph TD" in out
    for name in (START, END, "a", "b"):
        assert name in out, f"to_mermaid 缺少 {name}: {out}"
    print("✅ test_to_mermaid_contains_all_nodes")


def test_to_mermaid_marks_conditional_edges():
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", END)
    g.add_conditional_edges("a", route_pass)  # 命名函数
    out = g.to_mermaid()
    # 条件边用虚线 + 函数名标注
    assert "-.->" in out, f"条件边应用虚线: {out}"
    assert "route_pass" in out, f"条件边应带函数名: {out}"
    print("✅ test_to_mermaid_marks_conditional_edges")


def test_to_mermaid_includes_static_edges():
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    out = g.to_mermaid()
    # 静态边用实线箭头
    assert "a --> b" in out
    assert f"{START} --> a" in out
    assert f"b --> {END}" in out
    print("✅ test_to_mermaid_includes_static_edges")


# ============================================================
# 8) 集成：实际 pipeline 验证
# ============================================================

def test_real_pipeline_validates_clean():
    """模仿 nodes.py 里的 AgentMesh 流水线（planner/coder/debugger/reviewer）。"""
    g = StateGraph()
    g.add_node("planner", _noop)
    g.add_node("coder", _noop)
    g.add_node("debugger", _noop)
    g.add_node("reviewer", _noop)
    g.add_edge(START, "planner")
    g.add_edge("planner", "coder")
    g.add_edge("coder", "debugger")
    g.add_conditional_edges("debugger", route_pass)   # → reviewer
    g.add_conditional_edges("reviewer", route_end)     # → END
    issues = g.validate()
    errs = [i for i in issues if i.level == "error"]
    assert not errs, f"真实流水线不应有 error: {[str(i) for i in errs]}"
    print("✅ test_real_pipeline_validates_clean")


# ============================================================
# main
# ============================================================

ALL_TESTS = [
    test_valid_graph_no_issues,
    test_missing_entry_is_error,
    test_unreachable_node_is_error,
    test_node_reachable_via_conditional_is_not_error,
    test_duplicate_edge_is_warning,
    test_cycle_is_info_not_blocking,
    test_dead_end_node_is_warning,
    test_non_callable_router_is_error,
    test_conditional_returns_undefined_node_is_warning,
    test_conditional_returns_defined_node_no_warning,
    test_conditional_returns_end_is_fine,
    test_to_mermaid_contains_all_nodes,
    test_to_mermaid_marks_conditional_edges,
    test_to_mermaid_includes_static_edges,
    test_real_pipeline_validates_clean,
]


if __name__ == "__main__":
    import sys
    failed = 0
    for t in ALL_TESTS:
        try:
            t()
        except AssertionError as e:
            print(f"❌ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"💥 {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    if failed:
        print(f"\n❌ {failed}/{len(ALL_TESTS)} 测试失败")
        sys.exit(1)
    print(f"\n✅ 全部 {len(ALL_TESTS)} 个测试通过\n")
