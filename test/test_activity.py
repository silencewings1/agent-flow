"""验证 activity 缓存机制的正确性。

覆盖以下场景：
1. 首次调用 → fn 执行一次，结果返回
2. 中断恢复后再次调用 → fn 不再执行，返回缓存结果
3. 不同 activity_key → 各自独立缓存
4. 不同 thread → 缓存互不干扰
5. fn 抛异常 → 异常被记录，重入时重抛
6. 复杂类型（dict/list）的序列化/反序列化
"""
from __future__ import annotations

from collections import Counter

from agentflow import Checkpointer, Command, StateGraph, StateSchema, START, END

# —— 工具：可计数的 activity fn —— #

call_count: Counter = Counter()


def reset_counts():
    call_count.clear()


def make_activity_node(name: str, key: str, return_value: str = "hello"):
    """生成一个节点，内部调用 ctx.activity(key, fn)。"""
    def fn(state, ctx):
        result = ctx.activity(key, lambda: _do(name, key, return_value))
        return {"result": result, "log": [f"[{name}] {key} → {result}"]}
    return fn


def _do(node: str, key: str, return_value: str) -> str:
    call_count[(node, key)] += 1
    return return_value


# —— 测试用例 —— #

def test_first_call_executes_fn():
    """首次调用 activity：fn 应该被执行一次。"""
    reset_counts()
    g = StateGraph(StateSchema())
    g.add_node("a", make_activity_node("a", "x"))
    g.add_edge(START, "a")
    g.add_edge("a", END)
    app = g.compile(Checkpointer())

    r = app.invoke({}, thread_id="t_first")
    assert r.status == "completed"
    assert call_count[("a", "x")] == 1, f"fn 应执行 1 次，实际 {call_count[('a', 'x')]}"
    assert r.state["result"] == "hello"
    print("✅ test_first_call_executes_fn 通过")


def test_cached_on_resume():
    """中断恢复后再次调用 activity：fn 不应再次执行，返回缓存结果。"""
    reset_counts()

    def gate_node(state, ctx):
        # 先做一次 activity，再 interrupt
        result = ctx.activity("llm", lambda: _do("gate", "llm", "cached_val"))
        decision = ctx.interrupt({"ask": "go?", "result": result})
        return {"result": result, "decision": decision}

    g = StateGraph(StateSchema())
    g.add_node("gate", gate_node)
    g.add_edge(START, "gate")
    g.add_edge("gate", END)
    app = g.compile(Checkpointer())

    # 首次执行：gate 执行，activity 执行 fn，然后 interrupt
    r1 = app.invoke({}, thread_id="t_resume")
    assert r1.status == "interrupted"
    assert call_count[("gate", "llm")] == 1, f"首次 fn 应执行 1 次"

    # 恢复执行：gate 重入，activity 应命中缓存
    r2 = app.invoke({}, thread_id="t_resume", command=Command(resume="yes"))
    assert r2.status == "completed"
    # 关键断言：fn 仍然只执行 1 次（命中缓存）
    assert call_count[("gate", "llm")] == 1, f"恢复后 fn 不应再执行，实际 {call_count[('gate', 'llm')]}"
    assert r2.state["result"] == "cached_val"
    assert r2.state["decision"] == "yes"
    print("✅ test_cached_on_resume 通过")


def test_different_keys_independent():
    """不同 activity_key 的缓存各自独立。"""
    reset_counts()

    def multi_key_node(state, ctx):
        r1 = ctx.activity("key_a", lambda: _do("mk", "key_a", "val_a"))
        r2 = ctx.activity("key_b", lambda: _do("mk", "key_b", "val_b"))
        return {"r1": r1, "r2": r2}

    g = StateGraph(StateSchema())
    g.add_node("mk", multi_key_node)
    g.add_edge(START, "mk")
    g.add_edge("mk", END)
    app = g.compile(Checkpointer())

    r = app.invoke({}, thread_id="t_keys")
    assert r.status == "completed"
    assert call_count[("mk", "key_a")] == 1
    assert call_count[("mk", "key_b")] == 1
    assert r.state["r1"] == "val_a"
    assert r.state["r2"] == "val_b"

    # 再跑一次同一个 thread（节点重入），两个 key 都应命中缓存
    r2 = app.invoke({}, thread_id="t_keys")
    assert call_count[("mk", "key_a")] == 1, "key_a 缓存命中"
    assert call_count[("mk", "key_b")] == 1, "key_b 缓存命中"
    print("✅ test_different_keys_independent 通过")


def test_different_threads_independent():
    """不同 thread_id 的缓存互不干扰。"""
    reset_counts()

    def simple_node(state, ctx):
        r = ctx.activity("llm", lambda: _do("tn", "llm", f"thread_{state.get('tid','?')}"))
        return {"result": r}

    g = StateGraph(StateSchema())
    g.add_node("tn", simple_node)
    g.add_edge(START, "tn")
    g.add_edge("tn", END)
    cp = Checkpointer()
    app = g.compile(cp)

    r1 = app.invoke({"tid": "A"}, thread_id="t_a")
    r2 = app.invoke({"tid": "B"}, thread_id="t_b")
    assert r1.status == "completed"
    assert r2.status == "completed"
    # 两个 thread，fn 应执行 2 次
    assert call_count[("tn", "llm")] == 2, f"两个 thread 应各执行一次，实际 {call_count[('tn', 'llm')]}"
    assert r1.state["result"] == "thread_A"
    assert r2.state["result"] == "thread_B"
    print("✅ test_different_threads_independent 通过")


def test_exception_is_cached():
    """fn 抛异常时：异常被记录，重入时重抛，不再执行 fn。"""
    reset_counts()

    attempt = [0]

    def failing_fn():
        attempt[0] += 1
        raise ValueError("模拟失败")

    def risky_node(state, ctx):
        try:
            result = ctx.activity("risky", failing_fn)
            return {"result": result}
        except Exception as e:
            return {"error": str(e)}

    g = StateGraph(StateSchema())
    g.add_node("risky", risky_node)
    g.add_edge(START, "risky")
    g.add_edge("risky", END)
    app = g.compile(Checkpointer())

    # 第一次：fn 执行并抛出异常
    r1 = app.invoke({}, thread_id="t_exc")
    assert r1.status == "completed"
    assert r1.state["error"] == "模拟失败"
    assert attempt[0] == 1, f"fn 应执行 1 次，实际 {attempt[0]}"

    # 第二次：同一 thread，应命中异常缓存，fn 不再执行
    r2 = app.invoke({}, thread_id="t_exc")
    assert r2.status == "completed"
    assert r2.state["error"] == "模拟失败"
    assert attempt[0] == 1, f"重入时 fn 不应再次执行，实际 {attempt[0]}"
    print("✅ test_exception_is_cached 通过")


def test_complex_types():
    """复杂类型（dict/list）的序列化/反序列化正确。"""
    reset_counts()

    def complex_fn():
        return {
            "numbers": [1, 2, 3],
            "nested": {"a": 1, "b": [4, 5]},
            "text": "你好",
        }

    def complex_node(state, ctx):
        result = ctx.activity("complex", complex_fn)
        return {"result": result}

    g = StateGraph(StateSchema())
    g.add_node("cpx", complex_node)
    g.add_edge(START, "cpx")
    g.add_edge("cpx", END)
    app = g.compile(Checkpointer())

    r = app.invoke({}, thread_id="t_cpx")
    assert r.status == "completed"
    assert r.state["result"]["numbers"] == [1, 2, 3]
    assert r.state["result"]["nested"]["a"] == 1
    assert r.state["result"]["nested"]["b"] == [4, 5]
    assert r.state["result"]["text"] == "你好"

    # 再次调用：缓存命中，结果应一致
    r2 = app.invoke({}, thread_id="t_cpx")
    assert r2.state["result"]["numbers"] == [1, 2, 3]
    print("✅ test_complex_types 通过")


def test_activity_without_checkpointer():
    """没有 checkpointer 时 activity 退化为直接调用。"""
    reset_counts()

    # 直接构造 NodeContext，不设 _cp
    from agentflow.graph import NodeContext
    ctx = NodeContext("test", 1, 0)  # _cp is None by default
    result = ctx.activity("x", lambda: _do("nc", "x", "direct"))
    assert result == "direct"
    assert call_count[("nc", "x")] == 1
    print("✅ test_activity_without_checkpointer 通过")


if __name__ == "__main__":
    test_first_call_executes_fn()
    test_cached_on_resume()
    test_different_keys_independent()
    test_different_threads_independent()
    test_exception_is_cached()
    test_complex_types()
    test_activity_without_checkpointer()
    print("\n✅ 全部 activity 测试通过\n")
