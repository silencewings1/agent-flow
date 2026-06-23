"""Scenario 1: development pipeline with test loop and human-in-the-loop resume."""

from agentflow import Checkpointer, Command

from .common import banner, build_configured_graph


def run_pipeline() -> None:
    banner("场景 1 — 研发流水线：测试回环 + 人在回路中断/恢复")
    cp = Checkpointer()  # 进程内 SQLite；换成文件路径即可跨进程持久化
    app = build_configured_graph("pipeline", cp)
    tid = "feat-login"

    init = {
        "requirement": "实现登录接口，加上单元测试，写好文档",
        "pass_at_version": 3,   # 第 3 版代码才通过测试 → 触发两次回环
    }
    res = app.invoke(init, thread_id=tid)
    print(f"\n→ 第一次返回: status={res.status}, step={res.step}")
    assert res.status == "interrupted", "应在 human_review 处中断等待人工"
    print(f"  中断载荷(等待人工): {res.interrupt_payload}")

    # —— 模拟人工：先打回一次，看它退回 Coder 再回到评审 —— #
    print("\n  [人工] 第一次评审 → 打回（approve=False）")
    res = app.invoke({}, thread_id=tid, command=Command(resume={"approve": False}))
    print(f"→ 恢复后返回: status={res.status}, step={res.step}")
    assert res.status == "interrupted"

    print("\n  [人工] 第二次评审 → 合并（approve=True）")
    res = app.invoke({}, thread_id=tid, command=Command(resume={"approve": True}))
    print(f"→ 最终返回: status={res.status}, step={res.step}")
    assert res.status == "completed"

    print(f"\n  最终代码版本: v{res.state['code_version']}  approved={res.state['approved']}")
    print("  执行轨迹:")
    for line in res.state["log"]:
        print(f"    {line}")


if __name__ == "__main__":
    run_pipeline()