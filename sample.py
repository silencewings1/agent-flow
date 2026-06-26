"""Sample: 完整研发流水线（Planner → Coder → Debugger 回环）。

从 llm_config.json 加载 LLM 配置，节点使用真实 API 调用。
依赖：
    source ~/.py_ai/bin/activate
    PYTHONPATH=. python sample.py
"""

import os
import tempfile

from agentflow import Checkpointer, Command, StateGraph, StateSchema, START, END, append_reducer
from agentflow.llm import LLMRegistry
from agentflow.nodes import (
    planner, coder, debugger, ai_review, human_review,
    route_after_debug, route_after_human_review, set_registry,
)


def build_pipeline() -> StateGraph:
    """构建 planner → coder → debugger → ai_review → human_review 流水线。"""
    schema = StateSchema(reducers={"log": append_reducer})
    g = StateGraph(schema, max_steps=30)

    g.add_node("planner", planner)
    g.add_node("coder", coder)
    g.add_node("debugger", debugger)
    g.add_node("ai_review", ai_review)
    g.add_node("human_review", human_review)

    g.add_edge(START, "planner")
    g.add_edge("planner", "coder")
    g.add_edge("coder", "debugger")
    g.add_edge("ai_review", "human_review")

    # debugger: 测试通过 → ai_review，失败 → 退回 coder
    g.add_conditional_edges("debugger", route_after_debug)
    # human_review: 批准 → END，打回 → 退回 coder
    g.add_conditional_edges("human_review", route_after_human_review)

    return g


def main() -> None:
    # 1) 加载 LLM 配置
    reg = LLMRegistry.load("llm_config.json")
    set_registry(reg)
    print(f"  加载 llm_config.json, providers={list(reg._providers.keys())}")

    # 2) 构建并编译流水线
    cp = Checkpointer()
    app = build_pipeline().compile(cp)

    # 3) 准备 workdir（让 Coder 写文件）
    workdir = tempfile.mkdtemp(prefix="af-sample-")
    print(f"  workdir: {workdir}")

    init = {
        "requirement": "实现 fibonacci 函数, 写单元测试, 补文档",
        "workdir": workdir,
    }
    tid = "sample-run"

    # 4) 首次运行
    res = app.invoke(init, thread_id=tid)
    print(f"\n→ 首次返回: status={res.status}, step={res.step}")

    if res.status == "interrupted":
        print(f"  中断载荷: {res.interrupt_payload}")

        # 模拟人工：打回一次，让流水线再走一轮
        print("\n  [人工] 第一次评审 → 打回（approve=False）")
        res = app.invoke({}, thread_id=tid, command=Command(resume={"approve": False}))
        print(f"→ 恢复后: status={res.status}, step={res.step}")

        if res.status == "interrupted":
            print("\n  [人工] 第二次评审 → 合并（approve=True）")
            res = app.invoke({}, thread_id=tid, command=Command(resume={"approve": True}))
            print(f"→ 最终: status={res.status}, step={res.step}")

    # 5) 输出结果
    print(f"\n  最终代码版本: v{res.state.get('code_version', '?')}")
    print(f"  approved: {res.state.get('approved', 'N/A')}")
    print(f"  tests_passed: {res.state.get('tests_passed', 'N/A')}")

    for line in res.state.get("log", []):
        print(f"    {line}")

    # 列出生成的源码文件
    artifacts = res.state.get("artifacts", [])
    if artifacts:
        print(f"\n  生成文件 ({len(artifacts)} 个):")
        for art in artifacts:
            full = os.path.join(workdir, art) if not os.path.isabs(art) else art
            if os.path.isfile(full):
                size = os.path.getsize(full)
                print(f"    {art} ({size} bytes)")


if __name__ == "__main__":
    main()
