"""验证核心不变量：恢复时已完成节点绝不重跑（对应报告的硬约束）。

用一个全局计数器记录每个节点真实被调用的次数；中断恢复后，断言中断点
之前的节点调用次数没有增加。
"""

from collections import Counter

from agentflow import Checkpointer, Command, StateGraph, StateSchema, START, END

calls: Counter = Counter()


def make(name, ret=None):
    def fn(state, ctx):
        calls[name] += 1
        return ret or {}
    return fn


def gate(state, ctx):
    """在此请求人工放行：首次中断，恢复后返回。"""
    calls["gate"] += 1
    decision = ctx.interrupt({"ask": "go?"})
    return {"decision": decision}


def build():
    g = StateGraph(StateSchema())
    g.add_node("a", make("a"))
    g.add_node("b", make("b"))
    g.add_node("gate", gate)
    g.add_node("c", make("c"))
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", "gate")
    g.add_edge("gate", "c")
    g.add_edge("c", END)
    return g.compile(Checkpointer())


def test_no_rerun_on_resume():
    calls.clear()
    app = build()
    tid = "t1"

    r1 = app.invoke({}, thread_id=tid)
    assert r1.status == "interrupted", r1.status
    # a, b 各跑一次，gate 跑一次（抛中断）
    assert calls["a"] == 1 and calls["b"] == 1, dict(calls)
    assert calls["gate"] == 1, dict(calls)
    assert calls["c"] == 0, "c 在中断前不应执行"

    r2 = app.invoke({}, thread_id=tid, command=Command(resume="yes"))
    assert r2.status == "completed", r2.status
    # 关键断言：恢复后 a、b 不再重跑（仍为 1），gate 重入一次完成，c 首次执行
    assert calls["a"] == 1, f"a 被重跑了！{dict(calls)}"
    assert calls["b"] == 1, f"b 被重跑了！{dict(calls)}"
    assert calls["gate"] == 2, dict(calls)
    assert calls["c"] == 1, dict(calls)
    assert r2.state["decision"] == "yes"
    print("✅ test_no_rerun_on_resume 通过:", dict(calls))


def test_cycle_terminates():
    """条件边回环 + max_steps 兜底。"""
    g = StateGraph(StateSchema(), max_steps=5)
    g.add_node("loop", make("loop"))
    g.add_edge(START, "loop")
    g.add_conditional_edges("loop", lambda s: "loop")  # 永远指回自己
    app = g.compile(Checkpointer())
    r = app.invoke({}, thread_id="cyc")
    assert r.status == "failed" and "max_steps" in r.error
    print("✅ test_cycle_terminates 通过:", r.error)


if __name__ == "__main__":
    test_no_rerun_on_resume()
    test_cycle_terminates()
    print("\n✅ 全部测试通过\n")