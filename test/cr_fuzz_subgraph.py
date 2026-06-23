"""CR 对抗性 fuzz 脚本：覆盖 plan 未测的子图边界场景。

运行：PYTHONPATH=. python test/cr_fuzz_subgraph.py
"""

from collections import Counter

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

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}: {detail}")


# —— Fuzz 1: 嵌套 3 层子图 —— #


def fuzz_nested_3_levels():
    print("\n[Fuzz 1] 嵌套 3 层子图")

    def inner_fn(state, ctx):
        return {"deep": f"d:{state.get('seed', '')}"}

    inner = StateGraph()
    inner.add_node("iw", inner_fn)
    inner.add_edge(START, "iw")
    inner.add_edge("iw", END)

    def middle_fn(state, ctx):
        return {"mid": f"m:{state.get('val', '')}"}

    middle = StateGraph()
    middle.add_node("mw", middle_fn)
    middle.add_subgraph("in", inner.compile(Checkpointer()),
                        input_map={"val": "seed"}, output_map={"deep": "deep_r"})
    middle.add_edge(START, "mw")
    middle.add_edge("mw", "in")
    middle.add_edge("in", END)

    def outer_fn(state, ctx):
        return {"outer": f"o:{state.get('root', '')}"}

    outer = StateGraph()
    outer.add_node("ow", outer_fn)
    outer.add_subgraph("mid_sub", middle.compile(Checkpointer()),
                       input_map={"root": "val"},
                       output_map={"mid": "mid_r", "deep_r": "final_deep"})
    outer.add_edge(START, "ow")
    outer.add_edge("ow", "mid_sub")
    outer.add_edge("mid_sub", END)

    main = StateGraph()
    main.add_subgraph("out", outer.compile(Checkpointer()),
                      input_map={"input": "root"},
                      output_map={"outer": "outer_r", "mid_r": "mid_r",
                                  "final_deep": "final_deep"})
    main.add_edge(START, "out")
    main.add_edge("out", END)

    res = main.compile(Checkpointer()).invoke({"input": "X"}, thread_id="fuzz-3level")
    check("3 层嵌套 status=completed", res.status == "completed", res.status)
    check("3 层嵌套 outer_r", res.state.get("outer_r") == "o:X", res.state)
    check("3 层嵌套 mid_r", res.state.get("mid_r") == "m:X", res.state)
    check("3 层嵌套 final_deep", res.state.get("final_deep") == "d:X", res.state)


# —— Fuzz 2: 子图内 Send 动态扇出 —— #


def fuzz_subgraph_with_send():
    print("\n[Fuzz 2] 子图内 Send 动态扇出")

    def sub_split(state, ctx):
        return {"items": [1, 2, 3]}

    def sub_route(state):
        return [Send("w", {"item": i}, key=str(i)) for i in state["items"]]

    def sub_worker(state, ctx):
        return {"fanout": {ctx.instance_id: state["item"] * 100}}

    def sub_join(state, ctx):
        vals = sorted(state.get("fanout", {}).values())
        return {"result": f"join:{vals}"}

    sub = StateGraph(StateSchema(reducers={"fanout": fanout_reducer}))
    sub.add_node("split", sub_split)
    sub.add_node("w", sub_worker)
    sub.add_node("join", sub_join)
    sub.add_edge(START, "split")
    sub.add_conditional_edges("split", sub_route)
    sub.add_edge("w", "join")
    sub.add_edge("join", END)

    main = StateGraph()
    main.add_subgraph("fanout_sub", sub.compile(Checkpointer()),
                      output_map={"result": "review"})
    main.add_edge(START, "fanout_sub")
    main.add_edge("fanout_sub", END)

    res = main.compile(Checkpointer()).invoke({}, thread_id="fuzz-send-in-sub")
    check("子图内 Send status=completed", res.status == "completed", res.status)
    check("子图内 Send 结果", res.state.get("review") == "join:[100, 200, 300]",
          res.state.get("review"))


# —— Fuzz 3: resume 后子图 activity cache 命中 —— #


def fuzz_activity_cache_after_resume():
    print("\n[Fuzz 3] resume 后子图 activity cache 命中（工具不重跑）")
    tool_calls = Counter()

    def sub_analyze(state, ctx):
        # activity 调用：首次执行写入 cache，resume 后应命中
        val = ctx.activity("expensive_op", lambda: (tool_calls.__setitem__("op", tool_calls["op"] + 1) or 42))
        return {"pre": val, "log": ["analyze"]}

    def sub_gate(state, ctx):
        decision = ctx.interrupt({"ask": "go?"})
        return {"decision": decision, "log": ["gate"]}

    sub = StateGraph(StateSchema(reducers={"log": append_reducer}))
    sub.add_node("analyze", sub_analyze)
    sub.add_node("gate", sub_gate)
    sub.add_edge(START, "analyze")
    sub.add_edge("analyze", "gate")
    sub.add_edge("gate", END)

    main = StateGraph(StateSchema(reducers={"log": append_reducer}))
    main.add_subgraph("child", sub.compile(Checkpointer()),
                      output_map={"decision": "dec", "log": "log"})
    main.add_edge(START, "child")
    main.add_edge("child", END)

    cp = Checkpointer()
    app = main.compile(cp)
    tid = "fuzz-actcache"

    r1 = app.invoke({}, thread_id=tid)
    check("首次 interrupted", r1.status == "interrupted", r1.status)
    check("activity 首次执行 1 次", tool_calls["op"] == 1, dict(tool_calls))

    r2 = app.invoke({}, thread_id=tid, command=Command(resume="ok"))
    check("resume completed", r2.status == "completed", r2.status)
    check("activity resume 后仍 1 次（cache 命中）",
          tool_calls["op"] == 1, dict(tool_calls))


# —— Fuzz 4: 父图回环导致子图多次重入 —— #


def fuzz_subgraph_reentry_in_loop():
    print("\n[Fuzz 4] 父图回环导致子图多次重入（每次 thread_id 独立）")
    sub_runs = Counter()

    def sub_work(state, ctx):
        sub_runs["run"] += 1
        return {"v": state.get("code_version", 0) + 1}

    sub = StateGraph()
    sub.add_node("w", sub_work)
    sub.add_edge(START, "w")
    sub.add_edge("w", END)

    def parent_route(state):
        v = state.get("code_version", 0)
        if v >= 2:
            return END
        return "bump"

    def bump(state, ctx):
        return {"code_version": state.get("code_version", 0) + 1, "log": [f"bump v{state.get('code_version', 0) + 1}"]}

    main = StateGraph(StateSchema(reducers={"log": append_reducer}))
    main.add_subgraph("sub1", sub.compile(Checkpointer()),
                      input_map={"code_version": "code_version"},
                      output_map={"v": "code_version"})
    main.add_node("bump", bump)
    main.add_edge(START, "sub1")
    main.add_conditional_edges("sub1", parent_route)
    main.add_edge("bump", "sub1")

    res = main.compile(Checkpointer()).invoke({}, thread_id="fuzz-loop")
    check("回环 status=completed", res.status == "completed", res.status)
    check("子图多次重入（sub_runs >= 2）", sub_runs["run"] >= 2, dict(sub_runs))


# —— Fuzz 5: output_map 指向不存在的 child key —— #


def fuzz_output_map_missing_key():
    print("\n[Fuzz 5] output_map 指向不存在的 child key（应跳过不报错）")

    def sub_work(state, ctx):
        return {"exists": "yes"}  # 不产生 "missing" key

    sub = StateGraph()
    sub.add_node("w", sub_work)
    sub.add_edge(START, "w")
    sub.add_edge("w", END)

    main = StateGraph()
    main.add_subgraph("s", sub.compile(Checkpointer()),
                      output_map={"exists": "got_exists", "missing": "got_missing"})
    main.add_edge(START, "s")
    main.add_edge("s", END)

    res = main.compile(Checkpointer()).invoke({}, thread_id="fuzz-missing")
    check("missing key status=completed", res.status == "completed", res.status)
    check("存在的 key 映射成功", res.state.get("got_exists") == "yes", res.state)
    check("缺失的 key 跳过", "got_missing" not in res.state, res.state)


# —— Fuzz 6: 子图中断 + 父图兄弟节点已完成（no-rerun 边界）—— #


def fuzz_subgraph_interrupt_with_sibling():
    print("\n[Fuzz 6] 子图中断 + 父图兄弟节点已完成（no-rerun 边界）")
    calls = Counter()

    def sibling(state, ctx):
        calls["sibling"] += 1
        return {"sibling_done": True, "log": ["sibling"]}

    def sub_gate(state, ctx):
        calls["gate"] += 1
        decision = ctx.interrupt({"ask": "go?"})
        return {"result": decision}

    sub = StateGraph()
    sub.add_node("gate", sub_gate)
    sub.add_edge(START, "gate")
    sub.add_edge("gate", END)

    main = StateGraph(StateSchema(reducers={"log": append_reducer}))
    main.add_node("sibling", sibling)
    main.add_subgraph("child", sub.compile(Checkpointer()),
                      output_map={"result": "child_result"})
    main.add_edge(START, "sibling")
    main.add_edge(START, "child")  # 并行
    main.add_edge("sibling", END)
    main.add_edge("child", END)

    cp = Checkpointer()
    app = main.compile(cp)
    tid = "fuzz-sibling"

    r1 = app.invoke({}, thread_id=tid)
    check("中断 status=interrupted", r1.status == "interrupted", r1.status)
    check("sibling 首次执行", calls["sibling"] == 1, dict(calls))
    check("sibling 更新已提交", r1.state.get("sibling_done") is True, r1.state)

    r2 = app.invoke({}, thread_id=tid, command=Command(resume="ok"))
    check("resume completed", r2.status == "completed", r2.status)
    check("sibling 不重跑（no-rerun）", calls["sibling"] == 1, dict(calls))
    check("gate 重入一次", calls["gate"] == 2, dict(calls))


# —— Fuzz 7: 空子图（无节点）—— #


def fuzz_empty_subgraph():
    print("\n[Fuzz 7] 空子图（START → END，无业务节点）")

    sub = StateGraph()
    sub.add_edge(START, END)

    main = StateGraph()
    main.add_subgraph("empty", sub.compile(Checkpointer()))
    main.add_edge(START, "empty")
    main.add_edge("empty", END)

    res = main.compile(Checkpointer()).invoke({}, thread_id="fuzz-empty")
    check("空子图 status=completed", res.status == "completed", res.status)


def main():
    fuzz_nested_3_levels()
    fuzz_subgraph_with_send()
    fuzz_activity_cache_after_resume()
    fuzz_subgraph_reentry_in_loop()
    fuzz_output_map_missing_key()
    fuzz_subgraph_interrupt_with_sibling()
    fuzz_empty_subgraph()
    print(f"\n{'='*50}")
    print(f"Fuzz 结果: {passed} passed, {failed} failed")
    if failed:
        import sys
        sys.exit(1)
    print("✅ 全部对抗性 fuzz 通过")


if __name__ == "__main__":
    main()