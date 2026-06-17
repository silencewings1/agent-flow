"""可运行 demo：把四节点接成研发流水线，演示 DAG + checkpointer 的全部能力。

运行：
    python demo.py

依次演示：
  场景 1 — 完整跑通（含测试-修复回环 + 人在回路中断/恢复）
  场景 2 — 并行扇出（同一 super-step 内多节点并发）
  场景 3 — 错误重试（节点前几次抛错、自动重试后成功）
  场景 4 — 时间旅行（打印 checkpoint 历史与事件日志）
  场景 5 — 每节点 LLM 配置（展示各节点解析到的 provider/model）
"""
from __future__ import annotations

from agentflow import (
    Checkpointer,
    Command,
    LLMRegistry,
    StateGraph,
    StateSchema,
    START,
    END,
    append_reducer,
)
from agentflow.nodes import (
    coder,
    debugger,
    planner,
    ai_review,
    human_review,
    route_after_debug,
    route_after_human_review,
)


def build_pipeline(checkpointer: Checkpointer):
    """需求 → 分解 → 开发 →(测试回环)→ AI 评审 → 人工审批(人在回路) → 完成。"""
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
    g.add_conditional_edges("debugger", route_after_debug)        # 失败→coder，通过→ai_review
    g.add_edge("ai_review", "human_review")                       # AI 评审后等待人工
    g.add_conditional_edges("human_review", route_after_human_review)  # 打回→coder，合并→END
    return g.compile(checkpointer)


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(f"  {title}")
    print("=" * 64)


def scenario_pipeline() -> None:
    banner("场景 1 — 研发流水线：测试回环 + 人在回路中断/恢复")
    cp = Checkpointer()  # 进程内 SQLite；换成文件路径即可跨进程持久化
    app = build_pipeline(cp)
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


def scenario_parallel() -> None:
    banner("场景 2 — 并行扇出：同一 super-step 内多节点并发")
    cp = Checkpointer()
    schema = StateSchema(reducers={"log": append_reducer, "artifacts": append_reducer})
    g = StateGraph(schema)

    def split(state, ctx):
        return {"log": ["[split] 扇出 3 个并行子任务"]}

    def make_worker(name):
        def worker(state, ctx):
            return {"artifacts": [f"{name} 产物"], "log": [f"[{name}] 完成"]}
        return worker

    def join(state, ctx):
        return {"log": [f"[join] 汇聚 {len(state['artifacts'])} 个产物: {state['artifacts']}"]}

    g.add_node("split", split)
    for w in ("w1", "w2", "w3"):
        g.add_node(w, make_worker(w))
    g.add_node("join", join)

    g.add_edge(START, "split")
    for w in ("w1", "w2", "w3"):          # split 扇出到 3 个 worker（同一 super-step 并行）
        g.add_edge("split", w)
        g.add_edge(w, "join")             # 3 个 worker 汇聚到 join
    g.add_edge("join", END)

    res = g.compile(cp).invoke({"artifacts": []}, thread_id="fanout")
    print(f"\n→ status={res.status}, step={res.step}（split/3并行/join = 3 个 super-step）")
    for line in res.state["log"]:
        print(f"    {line}")


def scenario_retry() -> None:
    banner("场景 3 — 节点错误重试")
    cp = Checkpointer()
    schema = StateSchema(reducers={"log": append_reducer})
    g = StateGraph(schema)

    def flaky(state, ctx):
        # 前两次 attempt 抛错，第三次成功
        if ctx.attempt < 2:
            raise RuntimeError(f"第 {ctx.attempt} 次尝试失败（模拟瞬时错误）")
        return {"log": [f"[flaky] 第 {ctx.attempt} 次尝试成功"]}

    g.add_node("flaky", flaky, retries=2)
    g.add_edge(START, "flaky")
    g.add_edge("flaky", END)

    res = g.compile(cp).invoke({}, thread_id="retry")
    print(f"\n→ status={res.status}")
    for line in res.state["log"]:
        print(f"    {line}")
    print("  事件日志(可见 node_retry):")
    for e in cp.events("retry"):
        if e["kind"] in ("node_retry", "node_ok"):
            print(f"    seq={e['seq']} {e['kind']} {e['payload']}")


def scenario_timetravel() -> None:
    banner("场景 4 — 时间旅行：checkpoint 历史 + 事件日志")
    cp = Checkpointer()
    app = build_pipeline(cp)
    tid = "tt"
    app.invoke({"requirement": "做个 CLI 工具", "pass_at_version": 2}, thread_id=tid)
    print("\n  checkpoint 历史(每个 super-step 一份):")
    for c in cp.history(tid):
        print(f"    step={c.step:>2} status={c.status:<11} "
              f"frontier={c.frontier} code_version={c.state.get('code_version')}")


def scenario_config() -> None:
    banner("场景 5 — 每节点 LLM 配置：provider/model 独立解析")
    # 内联一份完整配置：声明 providers + 每节点独立设置。
    # 所有 provider 定义都在这里，代码中不再硬编码任何厂商。
    reg = LLMRegistry(
        providers={
            "anthropic": {
                "base_url": "https://api.anthropic.com/v1/messages",
                "api_key_env": "ANTHROPIC_API_KEY",
                "model": "claude-opus-4-8",
                "protocol": "anthropic",
            },
            "openai": {
                "base_url": "https://api.openai.com/v1/chat/completions",
                "api_key_env": "OPENAI_API_KEY",
                "model": "gpt-4o",
                "protocol": "openai",
            },
            "volcengine": {
                "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
                "api_key_env": "VOLCENGINE_API_KEY",
                "model": "ark-code-latest",
                "protocol": "openai",
            },
        },
        defaults={"provider": "volcengine", "temperature": 0.3},
        nodes={
            "planner": {"model": "claude-opus-4-8", "system": "需求分析师"},
            "coder": {"provider": "volcengine", "model": "ark-code-latest"},
            "debugger": {"model": "claude-sonnet-4-6"},
            "reviewer": {"provider": "mock"},
        },
    )
    print()
    for n in ("planner", "coder", "debugger", "reviewer"):
        c = reg.config_for(n)
        key = c.api_key_env or "-"
        print(f"    {n:9} provider={c.provider:12} protocol={c.protocol or '-':10} model={c.model:20} key_env={key}")
    print("\n  说明：所有 provider 定义均来自配置文件，代码中不再硬编码任何厂商。")
    print("       set_registry(reg) 即可让流水线节点按此配置调用真实 API；")
    print("       未设置 key 的真实 provider 会报清晰错误，mock 始终可离线运行。")


def scenario_real_coder() -> None:
    banner("场景 6 — 真实 Coder：写文件到 workdir")
    import tempfile
    workdir = tempfile.mkdtemp(prefix="af-demo-")
    print(f"\n  workdir: {workdir}")

    cp = Checkpointer()
    schema = StateSchema(reducers={"log": append_reducer, "artifacts": append_reducer})
    g = StateGraph(schema, max_steps=10)
    g.add_node("planner", planner)
    g.add_node("coder", coder)
    g.add_edge(START, "planner")
    g.add_edge("planner", "coder")
    g.add_edge("coder", END)
    app = g.compile(cp)

    init = {
        "requirement": "实现 fibonacci 函数, 写单元测试",
        "workdir": workdir,
    }
    res = app.invoke(init, thread_id="real-coder")
    assert res.status == "completed"
    print(f"  artifacts: {res.state.get('artifacts')}")
    # 验证文件实际存在
    import os
    for art in res.state.get("artifacts", []):
        full = os.path.join(workdir, art)
        exists = os.path.isfile(full)
        size = os.path.getsize(full) if exists else 0
        print(f"  {art}: exists={exists}, size={size}")
        if exists:
            with open(full) as f:
                first_line = f.readline().rstrip()
            print(f"    第一行: {first_line}")

    # 清理
    import shutil
    shutil.rmtree(workdir, ignore_errors=True)


def scenario_real_debugger() -> None:
    banner("场景 7 — 真实 Debugger：pytest 回环")
    import os
    import tempfile
    workdir = tempfile.mkdtemp(prefix="af-demo-dbg-")
    print(f"\n  workdir: {workdir}")

    # 先写一个会失败的测试文件
    src_dir = os.path.join(workdir, "src")
    os.makedirs(src_dir, exist_ok=True)
    # 必须有 __init__.py 才能做 `from src.task_t1 import ...`
    with open(os.path.join(src_dir, "__init__.py"), "w") as f:
        f.write("# auto-generated package\n")
    with open(os.path.join(src_dir, "task_t1.py"), "w") as f:
        f.write("def fib(n):\n"
                "    if n <= 1: return n\n"
                "    return fib(n-1) + fib(n-2)\n")
    with open(os.path.join(src_dir, "test_fib.py"), "w") as f:
        f.write("from src.task_t1 import fib\n"
                "def test_fib_0():\n    assert fib(0) == 0\n"
                "def test_fib_5():\n    assert fib(5) == 99  # 故意写错\n")

    cp = Checkpointer()
    schema = StateSchema(reducers={"log": append_reducer, "artifacts": append_reducer})
    g = StateGraph(schema, max_steps=10)
    g.add_node("debugger", debugger)
    g.add_edge(START, "debugger")
    g.add_conditional_edges("debugger", route_after_debug)
    # 加一个 dummy coder（不改文件，只递增版本）
    def dummy_coder(state, ctx):
        v = state.get("code_version", 0) + 1
        return {"code_version": v, "log": [f"[Coder] 修复 v{v}"]}
    g.add_node("coder", dummy_coder)
    g.add_edge("coder", "debugger")  # 回环：coder 修完后 debugger 再测
    app = g.compile(cp)

    init = {
        "tasks": ["t1"],
        "code_version": 1,
        "workdir": workdir,
        "artifacts": ["src/task_t1.py"],
    }
    res = app.invoke(init, thread_id="real-dbg")
    # 第一次：测试失败 → 退回 coder
    assert res.status in ("completed", "failed"), f"unexpected status: {res.status}"
    if res.status == "completed":
        print(f"  状态: {res.status}")
        print(f"  tests_passed: {res.state.get('tests_passed')}")
        print(f"  test_failures: {res.state.get('test_failures')}")
    elif res.status == "failed":
        print(f"  状态: failed (超过 max_steps，可能是正确的回环)")
    for line in res.state.get("log", []):
        print(f"    {line}")

    import shutil
    shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    scenario_pipeline()
    scenario_parallel()
    scenario_retry()
    scenario_timetravel()
    scenario_config()
    scenario_real_coder()
    scenario_real_debugger()
    print("\n✅ 全部场景执行完毕\n")
