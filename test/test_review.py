"""P1-5: Review 分层（ai_review + human_review 独立节点）的测试。

执行方式：PYTHONPATH=. python3 test/test_review.py

核心不变量：
- ai_review 节点不调用 ctx.interrupt()，因此执行后不会停留在 interrupted
- human_review 节点调用 ctx.interrupt()，payload 必须含 ai_review 字段
- state["ai_review"] 是字符串，state["approved"] 是 bool
- 恢复时 approve=False → 退回 coder（不是 reviewer），approve=True → END
"""

from collections import Counter
from typing import Any

from agentflow import (
    Checkpointer,
    Command,
    StateGraph,
    StateSchema,
    START,
    END,
    append_reducer,
)
from agentflow.nodes import (
    planner,
    coder,
    debugger,
    ai_review,
    human_review,
    route_after_debug,
    route_after_human_review,
)


# 记录每个节点真实被调用的次数 + ai_review/human_review 的入参快照
calls: Counter = Counter()
ai_review_seen: dict[str, Any] = {}
human_review_decisions: list = []


def build_pipeline(checkpointer: Checkpointer):
    """planner → coder → debugger → ai_review → human_review → (END | coder)"""
    schema = StateSchema(reducers={"log": append_reducer})
    g = StateGraph(schema, max_steps=30)
    g.add_node("planner", planner)
    g.add_node("coder", coder)
    g.add_node("debugger", debugger)
    g.add_node("ai_review", _wrapped_ai_review)
    g.add_node("human_review", _wrapped_human_review)

    g.add_edge(START, "planner")
    g.add_edge("planner", "coder")
    g.add_edge("coder", "debugger")
    g.add_conditional_edges("debugger", route_after_debug)
    g.add_edge("ai_review", "human_review")
    g.add_conditional_edges("human_review", route_after_human_review)
    return g.compile(checkpointer)


def _wrapped_ai_review(state, ctx):
    calls["ai_review"] += 1
    ai_review_seen["code_version"] = state.get("code_version")
    ai_review_seen["has_ai_review_before"] = state.get("ai_review")
    return ai_review(state, ctx)


def _wrapped_human_review(state, ctx):
    calls["human_review"] += 1
    # 拦截 interrupt 之前的状态快照
    human_review_decisions.append({
        "ai_review_in_state": state.get("ai_review"),
        "code_version": state.get("code_version"),
    })
    return human_review(state, ctx)


def _reset():
    calls.clear()
    ai_review_seen.clear()
    human_review_decisions.clear()


# ============================================================
# 1) ai_review 不中断
# ============================================================

def test_ai_review_does_not_interrupt():
    """跑到 ai_review 节点后不应进入 interrupted 状态。"""
    _reset()
    cp = Checkpointer()
    app = build_pipeline(cp)
    tid = "t-ai-no-int"

    # pass_at_version=1 → debugger 一次就通过，立刻进入 ai_review
    r = app.invoke({"requirement": "做一个简单功能", "pass_at_version": 1},
                   thread_id=tid)
    # ai_review 不会调 interrupt，下一站是 human_review（会中断）
    assert r.status == "interrupted", f"应在 human_review 中断，实得 {r.status}"
    # 但 ai_review 必须已执行过
    assert calls["ai_review"] >= 1, dict(calls)
    # 关键断言：ai_review 不应被计算为"中断"状态来源
    # （即调用 ai_review 的节点上下文不应有 interrupt payload）
    assert r.interrupt_payload is not None
    # human_review 的 payload 应含 ai_review 字段
    assert "ai_review" in r.interrupt_payload
    print("✅ test_ai_review_does_not_interrupt:", dict(calls))


# ============================================================
# 2) human_review 中断，payload 含 ai_review
# ============================================================

def test_human_review_interrupts_with_ai_review_in_payload():
    """human_review 必须中断，且中断 payload 含 ai_review 字段。"""
    _reset()
    cp = Checkpointer()
    app = build_pipeline(cp)
    tid = "t-hi-payload"

    r = app.invoke({"requirement": "登录接口", "pass_at_version": 1},
                   thread_id=tid)
    assert r.status == "interrupted", r.status
    payload = r.interrupt_payload
    assert payload is not None
    # 关键断言：payload 必须含 ai_review（这是 review 分层的核心收益）
    assert "ai_review" in payload, payload
    assert isinstance(payload["ai_review"], str), type(payload["ai_review"])
    # 也应含 code_version、tasks 字段
    assert "code_version" in payload
    assert "tasks" in payload
    print("✅ test_human_review_interrupts_with_ai_review_in_payload")


# ============================================================
# 3) resume reject → 退回 coder（不退回 ai_review）
# ============================================================

def test_human_review_resume_reject_returns_to_coder():
    """人工打回时，路径应回到 coder（不是 ai_review）。"""
    _reset()
    cp = Checkpointer()
    app = build_pipeline(cp)
    tid = "t-reject"

    # 第一次跑通到 human_review
    r1 = app.invoke({"requirement": "登录", "pass_at_version": 3},
                    thread_id=tid)
    assert r1.status == "interrupted"
    # human_review 第一次调用 → 中断（counter=1）
    assert calls["ai_review"] == 1, dict(calls)
    assert calls["human_review"] == 1, dict(calls)
    code_version_after_first_interrupt = r1.state.get("code_version")
    assert code_version_after_first_interrupt == 3, code_version_after_first_interrupt

    # 人工打回
    r2 = app.invoke({}, thread_id=tid, command=Command(resume={"approve": False}))
    # 打回路径：human_review 重入取到 approve=False（counter=2）→ 回到 coder → v4 →
    # debugger 通过 → ai_review 跑第 2 次 → human_review 再次中断（counter=3）
    assert r2.status == "interrupted", f"应再次中断于 human_review，实得 {r2.status}"
    assert calls["ai_review"] == 2, dict(calls)
    # human_review: 第 1 次中断(1) + 第 1 次恢复(2) + 第 2 次中断(3) = 3
    assert calls["human_review"] == 3, dict(calls)
    # code_version 已递增到 4
    assert r2.state.get("code_version") == 4, r2.state.get("code_version")
    # 关键断言：state['approved'] 已是 False（被打回）
    assert r2.state.get("approved") is False
    # log 应包含 coder 重新执行的痕迹
    log_text = "\n".join(r2.state.get("log", []))
    assert "[Coder] 产出代码 v4" in log_text, log_text
    # 关键断言：打回后路径是 coder（不是 ai_review）—— 看 log 顺序
    assert "[Human Reviewer] 人工决定: 打回" in log_text
    assert log_text.index("[Coder] 产出代码 v4") > log_text.index("[Human Reviewer] 人工决定: 打回")
    print("✅ test_human_review_resume_reject_returns_to_coder:", dict(calls))


# ============================================================
# 4) resume approve → 走 END
# ============================================================

def test_human_review_resume_approve_returns_to_end():
    """人工合并时，应走 END（completed）。"""
    _reset()
    cp = Checkpointer()
    app = build_pipeline(cp)
    tid = "t-approve"

    r1 = app.invoke({"requirement": "登录", "pass_at_version": 1},
                    thread_id=tid)
    assert r1.status == "interrupted", r1.status

    r2 = app.invoke({}, thread_id=tid, command=Command(resume={"approve": True}))
    assert r2.status == "completed", f"approve=True 应走 END，实得 {r2.status}"
    assert r2.state.get("approved") is True, r2.state
    assert r2.state.get("ai_review"), "完成后 ai_review 字段应存在"
    print("✅ test_human_review_resume_approve_returns_to_end")


# ============================================================
# 5) state["ai_review"] 是字符串
# ============================================================

def test_state_ai_review_is_string():
    """完成后 state['ai_review'] 应是字符串。"""
    _reset()
    cp = Checkpointer()
    app = build_pipeline(cp)
    tid = "t-ai-str"

    r1 = app.invoke({"requirement": "做个 todo", "pass_at_version": 1},
                    thread_id=tid)
    assert r1.status == "interrupted"
    r2 = app.invoke({}, thread_id=tid, command=Command(resume={"approve": True}))
    assert r2.status == "completed"
    assert isinstance(r2.state.get("ai_review"), str), \
        f"ai_review 应是 str，实得 {type(r2.state.get('ai_review'))}"
    # mock LLM 也能保证非空
    assert r2.state["ai_review"], "ai_review 字符串不应为空"
    print("✅ test_state_ai_review_is_string:", repr(r2.state["ai_review"][:60]))


# ============================================================
# 6) state["approved"] 是 bool
# ============================================================

def test_state_approved_is_bool():
    """完成后 state['approved'] 应是 bool。"""
    _reset()
    cp = Checkpointer()
    app = build_pipeline(cp)
    tid = "t-approved-bool"

    r1 = app.invoke({"requirement": "做个 todo", "pass_at_version": 1},
                    thread_id=tid)
    assert r1.status == "interrupted"
    r2 = app.invoke({}, thread_id=tid, command=Command(resume={"approve": True}))
    assert r2.status == "completed"
    approved = r2.state.get("approved")
    assert isinstance(approved, bool), f"approved 应是 bool，实得 {type(approved)}"
    assert approved is True

    # 再测一次 reject 路径
    _reset()
    cp2 = Checkpointer()
    app2 = build_pipeline(cp2)
    tid2 = "t-approved-bool-2"
    r3 = app2.invoke({"requirement": "做个 todo", "pass_at_version": 1},
                     thread_id=tid2)
    r4 = app2.invoke({}, thread_id=tid2, command=Command(resume={"approve": False}))
    # 打回后会回到 coder；第 2 次到 human_review 仍会中断
    assert r4.status == "interrupted", r4.status
    # 此时 state["approved"] 应是 False（已被设置过）
    assert r4.state.get("approved") is False
    print("✅ test_state_approved_is_bool")


if __name__ == "__main__":
    test_ai_review_does_not_interrupt()
    test_human_review_interrupts_with_ai_review_in_payload()
    test_human_review_resume_reject_returns_to_coder()
    test_human_review_resume_approve_returns_to_end()
    test_state_ai_review_is_string()
    test_state_approved_is_bool()
    print("\n✅ 全部 6 个测试通过\n")