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


def test_entry_to_undefined_node_is_error():
    """add_edge(START, 'ghost') 且 'ghost' 未注册时，validate() 应报 error。"""
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_edge(START, "ghost")  # 入口指向未定义节点
    g.add_edge("ghost", "a")
    issues = g.validate()
    errs = [i for i in issues if i.level == "error"]
    assert any("未定义" in i.message for i in errs), [str(i) for i in errs]
    # compile() 也应抛 ValueError
    try:
        g.compile()
        assert False, "compile() 应抛 ValueError"
    except ValueError as e:
        assert "ghost" in str(e)
    print("✅ test_entry_to_undefined_node_is_error")


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

def test_node_with_no_edges_is_valid_terminal():
    """无出边节点在 LangGraph 语义中是合法终端，不应报 error/warning。

    「无出边」视作汇点（与 _reaches_end 保持一致），如果它本身不可达
    （如孤立节点），会由可达性检查单独处理（unreachable error）。
    """
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_node("dead", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", "dead")   # dead 没有任何出边
    issues = g.validate()
    # 「无出边」视作合法终端节点，所以 dead 不应触发任何 issue
    related = [i for i in issues if i.node == "dead"]
    assert not related, f"无出边节点不应触发 issue: {[str(i) for i in related]}"
    print("✅ test_node_with_no_edges_is_valid_terminal")


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


def test_conditional_returns_nested_function_not_extracted():
    """嵌套函数/lambda 内的 return 字符串不应被外层路由器提取（避免误报）。

    复现：路由函数内部定义了一个辅助函数，辅助函数 return 一个未定义节点名。
    修复前会把 "ghost" 当作外层路由的可能目标，给出 "未定义节点" warning。
    修复后跳过嵌套子树，只看外层 return "b"。
    """
    def route_with_nested(state):
        def helper():
            return "ghost"  # 嵌套函数 return，不应被外层路由器提取
        return "b"

    g = StateGraph()
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_edge(START, "a")
    g.add_edge("a", END)
    g.add_conditional_edges("a", route_with_nested)
    issues = g.validate()
    warns = [i for i in issues if i.level == "warning"]
    assert not any("未定义节点" in i.message for i in warns), \
        f"嵌套函数 return 不应触发未定义节点 warning: {[str(i) for i in warns]}"
    assert not any("ghost" in i.message for i in warns), \
        f"嵌套函数返回的 'ghost' 不应被外层路由器提取: {[str(i) for i in warns]}"
    print("✅ test_conditional_returns_nested_function_not_extracted")


def test_conditional_src_undefined_node_is_error():
    """add_conditional_edges("ghost", ...) 且 ghost 未注册时，validate() 应报 error。"""
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_edge(START, "a")
    g.add_conditional_edges("ghost", route_to_b)  # 源节点 ghost 未注册
    issues = g.validate()
    errs = [i for i in issues if i.level == "error"]
    assert any("ghost" in i.message for i in errs), \
        [str(i) for i in errs]
    # compile() 也应抛 ValueError
    try:
        g.compile()
        assert False, "compile() 应抛 ValueError"
    except ValueError as e:
        assert "ghost" in str(e)
    print("✅ test_conditional_src_undefined_node_is_error")


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


def test_barrier_edge_validates_clean_and_mermaid_marks_barrier():
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_node("b", _noop)
    g.add_node("join", _noop)
    g.add_edge(START, "a")
    g.add_edge(START, "b")
    g.add_edge(["a", "b"], "join")
    g.add_edge("join", END)
    issues = g.validate()
    errs = [i for i in issues if i.level == "error"]
    assert not errs, [str(i) for i in errs]
    out = g.to_mermaid()
    assert "barrier" in out
    assert "a -->|barrier| join" in out
    assert "b -->|barrier| join" in out
    print("✅ test_barrier_edge_validates_clean_and_mermaid_marks_barrier")


def test_barrier_edge_rejects_undefined_nodes():
    g = StateGraph()
    g.add_node("a", _noop)
    g.add_edge(START, "a")
    g.add_edge(["a", "ghost"], "join")
    issues = g.validate()
    errs = [i for i in issues if i.level == "error"]
    assert any("ghost" in i.message for i in errs), [str(i) for i in errs]
    assert any("join" in i.message for i in errs), [str(i) for i in errs]
    try:
        g.compile()
        assert False, "compile() 应抛 ValueError"
    except ValueError as e:
        assert "ghost" in str(e) or "join" in str(e)
    print("✅ test_barrier_edge_rejects_undefined_nodes")


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
    test_entry_to_undefined_node_is_error,
    test_unreachable_node_is_error,
    test_node_reachable_via_conditional_is_not_error,
    test_duplicate_edge_is_warning,
    test_cycle_is_info_not_blocking,
    test_node_with_no_edges_is_valid_terminal,
    test_non_callable_router_is_error,
    test_conditional_returns_undefined_node_is_warning,
    test_conditional_returns_defined_node_no_warning,
    test_conditional_returns_end_is_fine,
    test_conditional_returns_nested_function_not_extracted,
    test_to_mermaid_contains_all_nodes,
    test_to_mermaid_marks_conditional_edges,
    test_to_mermaid_includes_static_edges,
    test_barrier_edge_validates_clean_and_mermaid_marks_barrier,
    test_barrier_edge_rejects_undefined_nodes,
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
