"""Scenario 9: subgraph — a node that is itself a nested StateGraph.

演示父图节点内嵌套一个完整 CompiledGraph：
- 子图独立运行（自己的 super-step 循环、checkpointer、max_steps）
- 子图内 interrupt 冒泡到父图，resume 透传回子图
- 子图完成后经 output_map 把结果写回父 state
- 子图内已完成节点在父图 resume 后不重跑（硬不变量）
"""
from __future__ import annotations

from agentflow import (
    Checkpointer,
    Command,
    END,
    START,
    StateGraph,
    StateSchema,
    append_reducer,
)

from .common import banner


# —— 子图节点函数 —— #


def analyze_fn(state, ctx):
    """分析代码，产出 AI 评审意见（mock，不依赖 LLM）。"""
    code = state.get("code", "")
    snippet = code[:30].replace("\n", " ")
    ai_review = f"AI 评审完成：代码片段「{snippet}」，建议补充测试与边界处理。"
    return {"ai_review": ai_review, "log": [f"[analyze] {ai_review}"]}


def human_gate_fn(state, ctx):
    """人在回路：中断等待人工审批。"""
    decision = ctx.interrupt({
        "ask": "请评审并决定是否合并",
        "ai_review": state.get("ai_review", ""),
    })
    approved = (
        bool(decision.get("approve"))
        if isinstance(decision, dict)
        else bool(decision)
    )
    return {
        "approved": approved,
        "decision": decision,
        "log": [f"[human_gate] 人工决定: {'合并' if approved else '打回'}"],
    }


def summarize_fn(state, ctx):
    """汇总评审结论。"""
    result = f"review done, approved={state.get('approved')}"
    return {"result": result, "log": [f"[summarize] {result}"]}


# —— 父图节点函数 —— #


def prepare_fn(state, ctx):
    """准备待评审的代码与任务。"""
    return {
        "code": "def fib(n):\n    if n < 2:\n        return n\n    return fib(n-1) + fib(n-2)",
        "task": "review fib implementation",
        "log": ["[prepare] 代码与任务就绪"],
    }


def run_subgraph() -> None:
    banner("场景 9 — 子图：节点内嵌套 StateGraph，interrupt 冒泡到父图")

    cp = Checkpointer()

    # 子图：analyze → human_gate(interrupt) → summarize
    sub = StateGraph(StateSchema(reducers={"log": append_reducer}))
    sub.add_node("analyze", analyze_fn)
    sub.add_node("human_gate", human_gate_fn)
    sub.add_node("summarize", summarize_fn)
    sub.add_edge(START, "analyze")
    sub.add_edge("analyze", "human_gate")
    sub.add_edge("human_gate", "summarize")
    sub.add_edge("summarize", END)

    # 父图：prepare → code_review(子图) → END
    main = StateGraph(StateSchema(reducers={"log": append_reducer}))
    main.add_node("prepare", prepare_fn)
    main.add_subgraph(
        "code_review",
        sub.compile(cp),
        input_map={"code": "code", "task": "task"},
        output_map={"result": "review_result", "log": "log"},
    )
    main.add_edge(START, "prepare")
    main.add_edge("prepare", "code_review")
    main.add_edge("code_review", END)

    app = main.compile(cp)
    tid = "demo-subgraph"

    # 首次运行：子图跑到 human_gate 中断 → 冒泡到父图
    r1 = app.invoke({}, thread_id=tid)
    assert r1.status == "interrupted", r1.status
    print(f"\n→ 首次运行: status={r1.status}, step={r1.step}")
    print(f"  中断 payload: {r1.interrupt_payload['ask']}")
    print(f"  ai_review: {r1.interrupt_payload['ai_review'][:50]}...")

    # 恢复运行：resume 透传到子图 human_gate → 子图继续 summarize → 父图完成
    r2 = app.invoke({}, thread_id=tid, command=Command(resume={"approve": True}))
    assert r2.status == "completed", r2.status
    print(f"\n→ 恢复运行: status={r2.status}, step={r2.step}")
    print(f"  review_result: {r2.state.get('review_result')}")
    print("  执行日志:")
    for line in r2.state.get("log", []):
        print(f"    {line}")


if __name__ == "__main__":
    run_subgraph()
