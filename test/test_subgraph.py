"""P2-3: subgraph — a node that is itself a nested CompiledGraph."""

from collections import Counter

from agentflow import (
    Checkpointer,
    Command,
    END,
    START,
    StateGraph,
    StateSchema,
    append_reducer,
)


# —— 测试 1：子图独立运行并经 output_map 返回结果 —— #


def test_subgraph_runs_and_returns_output():
    calls = Counter()

    def sub_analyze(state, ctx):
        calls["analyze"] += 1
        return {"result": f"analyzed:{state.get('code', '')}"}

    sub = StateGraph()
    sub.add_node("analyze", sub_analyze)
    sub.add_edge(START, "analyze")
    sub.add_edge("analyze", END)

    def prepare(state, ctx):
        return {"code": "def fib(n): ..."}

    main = StateGraph()
    main.add_node("prepare", prepare)
    main.add_subgraph(
        "code_review",
        sub.compile(Checkpointer()),
        input_map={"code": "code"},
        output_map={"result": "review_result"},
    )
    main.add_edge(START, "prepare")
    main.add_edge("prepare", "code_review")
    main.add_edge("code_review", END)

    res = main.compile(Checkpointer()).invoke({}, thread_id="sub-basic")

    assert res.status == "completed", res.status
    assert res.state["review_result"] == "analyzed:def fib(n): ..."
    assert res.state["code"] == "def fib(n): ..."  # 父 state 保留
    assert calls["analyze"] == 1


# —— 测试 2：子图内 interrupt 冒泡到父图，resume 后父子都完成 —— #


def test_subgraph_interrupt_bubbles_to_parent():
    calls = Counter()

    def sub_gate(state, ctx):
        calls["gate"] += 1
        decision = ctx.interrupt({"ask": "approve?", "code": state.get("code")})
        return {"approved": bool(decision)}

    sub = StateGraph()
    sub.add_node("gate", sub_gate)
    sub.add_edge(START, "gate")
    sub.add_edge("gate", END)

    main = StateGraph()
    main.add_subgraph(
        "review",
        sub.compile(Checkpointer()),
        input_map={"code": "code"},
        output_map={"approved": "approved"},
    )
    main.add_edge(START, "review")
    main.add_edge("review", END)

    cp = Checkpointer()
    app = main.compile(cp)
    tid = "sub-interrupt"

    r1 = app.invoke({"code": "x=1"}, thread_id=tid)
    assert r1.status == "interrupted", r1.status
    assert r1.interrupt_payload == {"ask": "approve?", "code": "x=1"}
    assert calls["gate"] == 1

    r2 = app.invoke({}, thread_id=tid, command=Command(resume=True))
    assert r2.status == "completed", r2.status
    assert r2.state["approved"] is True
    assert calls["gate"] == 2  # 重入一次完成


# —— 测试 3：子图 max_steps 超限 → 父图 failed —— #


def test_subgraph_max_steps_failure_bubbles():
    def sub_loop(state, ctx):
        return {"log": ["loop"]}

    sub = StateGraph(StateSchema(reducers={"log": append_reducer}), max_steps=2)
    sub.add_node("loop", sub_loop)
    sub.add_edge(START, "loop")
    sub.add_conditional_edges("loop", lambda s: "loop")  # 死循环

    main = StateGraph()
    main.add_subgraph("looper", sub.compile(Checkpointer()))
    main.add_edge(START, "looper")
    main.add_edge("looper", END)

    res = main.compile(Checkpointer()).invoke({}, thread_id="sub-maxsteps")

    assert res.status == "failed", res.status
    assert "子图 looper 失败" in res.error
    assert "max_steps" in res.error


# —— 测试 4：子图 state 与父 state 隔离 —— #


def test_subgraph_state_isolation():
    def sub_node(state, ctx):
        # 子图内部写一堆字段，只有 output_map 声明的才应回到父 state
        return {
            "result": "ok",
            "internal_secret": "leak?",
            "internal_log": ["hidden"],
        }

    sub = StateGraph()
    sub.add_node("work", sub_node)
    sub.add_edge(START, "work")
    sub.add_edge("work", END)

    main = StateGraph()
    main.add_subgraph(
        "worker",
        sub.compile(Checkpointer()),
        input_map={"parent_input": "child_input"},
        output_map={"result": "parent_output"},
    )
    main.add_edge(START, "worker")
    main.add_edge("worker", END)

    res = main.compile(Checkpointer()).invoke(
        {"parent_input": "hello", "parent_keep": "kept"},
        thread_id="sub-isolation",
    )

    assert res.status == "completed", res.status
    assert res.state["parent_output"] == "ok"
    assert res.state["parent_keep"] == "kept"
    # input_map 映射的 child_input 不应泄漏回父
    assert "child_input" not in res.state
    # output_map 未声明的子图字段不应泄漏
    assert "internal_secret" not in res.state
    assert "internal_log" not in res.state
    assert "result" not in res.state  # output_map 把 result → parent_output


# —— 测试 5：嵌套 2 层子图 —— #


def test_nested_two_level_subgraph():
    def inner_fn(state, ctx):
        return {"deep": f"deep:{state.get('seed', '')}"}

    inner = StateGraph()
    inner.add_node("inner_work", inner_fn)
    inner.add_edge(START, "inner_work")
    inner.add_edge("inner_work", END)

    def middle_fn(state, ctx):
        return {"mid": f"mid:{state.get('val', '')}"}

    middle = StateGraph()
    middle.add_node("middle_work", middle_fn)
    middle.add_subgraph(
        "inner_sub",
        inner.compile(Checkpointer()),
        input_map={"val": "seed"},
        output_map={"deep": "deep_result"},
    )
    middle.add_edge(START, "middle_work")
    middle.add_edge("middle_work", "inner_sub")
    middle.add_edge("inner_sub", END)

    main = StateGraph()
    main.add_subgraph(
        "outer",
        middle.compile(Checkpointer()),
        input_map={"root": "val"},
        output_map={"mid": "mid_result", "deep_result": "final_deep"},
    )
    main.add_edge(START, "outer")
    main.add_edge("outer", END)

    res = main.compile(Checkpointer()).invoke(
        {"root": "ROOT"}, thread_id="sub-nested"
    )

    assert res.status == "completed", res.status
    assert res.state["mid_result"] == "mid:ROOT"
    assert res.state["final_deep"] == "deep:ROOT"


# —— 测试 6：子图内中断恢复后，子图已完成节点不重跑（硬不变量子图版）—— #


def test_subgraph_no_rerun_on_parent_resume():
    calls = Counter()

    def sub_a(state, ctx):
        calls["a"] += 1
        return {"log": ["a done"]}

    def sub_gate(state, ctx):
        calls["gate"] += 1
        decision = ctx.interrupt({"ask": "go?"})
        return {"decision": decision, "log": [f"gate:{decision}"]}

    def sub_b(state, ctx):
        calls["b"] += 1
        return {"log": ["b done"]}

    sub = StateGraph(StateSchema(reducers={"log": append_reducer}))
    sub.add_node("a", sub_a)
    sub.add_node("gate", sub_gate)
    sub.add_node("b", sub_b)
    sub.add_edge(START, "a")
    sub.add_edge("a", "gate")
    sub.add_edge("gate", "b")
    sub.add_edge("b", END)

    main = StateGraph(StateSchema(reducers={"log": append_reducer}))
    main.add_subgraph(
        "child",
        sub.compile(Checkpointer()),
        output_map={"log": "log"},
    )
    main.add_edge(START, "child")
    main.add_edge("child", END)

    cp = Checkpointer()
    app = main.compile(cp)
    tid = "sub-norerun"

    r1 = app.invoke({}, thread_id=tid)
    assert r1.status == "interrupted", r1.status
    assert calls["a"] == 1, f"a 应只跑 1 次，实际 {dict(calls)}"
    assert calls["gate"] == 1, f"gate 应只跑 1 次（中断），实际 {dict(calls)}"
    assert calls["b"] == 0, f"b 在中断前不应执行，实际 {dict(calls)}"

    r2 = app.invoke({}, thread_id=tid, command=Command(resume="yes"))
    assert r2.status == "completed", r2.status
    # 关键断言：子图内 a 不重跑（仍为 1），gate 重入一次完成（2），b 首次执行（1）
    assert calls["a"] == 1, f"子图 a 被重跑了！{dict(calls)}"
    assert calls["gate"] == 2, f"gate 次数不对: {dict(calls)}"
    assert calls["b"] == 1, f"b 应首次执行: {dict(calls)}"


# —— 附加：to_mermaid 识别子图节点 —— #


def test_to_mermaid_renders_subgraph_label():
    sub = StateGraph()
    sub.add_node("s", lambda state, ctx: {})
    sub.add_edge(START, "s")
    sub.add_edge("s", END)

    main = StateGraph()
    main.add_subgraph("code_review", sub.compile(Checkpointer()))
    main.add_edge(START, "code_review")
    main.add_edge("code_review", END)

    mermaid = main.to_mermaid()
    assert "子图: code_review" in mermaid


# —— 附加：add_subgraph 类型校验 —— #


def test_add_subgraph_rejects_non_compiled_graph():
    g = StateGraph()
    try:
        g.add_subgraph("bad", "not a graph")  # type: ignore[arg-type]
        assert False, "应拒绝非 CompiledGraph"
    except TypeError:
        pass


# —— 附加：空图修复验证 —— #


def test_empty_graph_completes():
    """空图 START → END（无业务节点）应直接 completed，不报 KeyError。"""
    g = StateGraph()
    g.add_edge(START, END)

    res = g.compile(Checkpointer()).invoke({"x": 1}, thread_id="empty-graph")
    assert res.status == "completed", res.status
    assert res.state["x"] == 1
    assert res.step == 0


def test_empty_subgraph_completes():
    """空子图（无业务节点）应在父图中正常运行并完成。"""
    sub = StateGraph()
    sub.add_edge(START, END)

    main = StateGraph()
    main.add_subgraph("noop", sub.compile(Checkpointer()))
    main.add_edge(START, "noop")
    main.add_edge("noop", END)

    res = main.compile(Checkpointer()).invoke({}, thread_id="empty-sub")
    assert res.status == "completed", res.status


# —— 附加：add_subgraph retries 参数 —— #


def test_add_subgraph_accepts_retries_param():
    """验证 add_subgraph 接受 retries / retry_backoff 参数。"""
    sub = StateGraph()
    sub.add_node("w", lambda s, c: {"ok": True})
    sub.add_edge(START, "w")
    sub.add_edge("w", END)

    main = StateGraph()
    main.add_subgraph("child", sub.compile(Checkpointer()),
                      output_map={"ok": "ok"},
                      retries=2, retry_backoff=0.1)
    main.add_edge(START, "child")
    main.add_edge("child", END)

    res = main.compile(Checkpointer()).invoke({}, thread_id="sub-retries")
    assert res.status == "completed", res.status
    assert res.state["ok"] is True


ALL_TESTS = [
    test_subgraph_runs_and_returns_output,
    test_subgraph_interrupt_bubbles_to_parent,
    test_subgraph_max_steps_failure_bubbles,
    test_subgraph_state_isolation,
    test_nested_two_level_subgraph,
    test_subgraph_no_rerun_on_parent_resume,
    test_to_mermaid_renders_subgraph_label,
    test_add_subgraph_rejects_non_compiled_graph,
    test_empty_graph_completes,
    test_empty_subgraph_completes,
    test_add_subgraph_accepts_retries_param,
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
    print(f"\n✅ 全部 {len(ALL_TESTS)} 个子图测试通过\n")