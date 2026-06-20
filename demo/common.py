"""Common helpers for runnable agent-flow demos."""
from __future__ import annotations

import os

from agentflow import (
    Checkpointer,
    Send,
    END,
    build_graph_from_config,
    load_graph_config,
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


CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "conf", "graph_config.example.json")


def split(state, ctx):
    return {"log": ["[split] 扇出 3 个并行子任务"]}


def worker_w1(state, ctx):
    return {"artifacts": ["w1 产物"], "log": ["[w1] 完成"]}


def worker_w2(state, ctx):
    return {"artifacts": ["w2 产物"], "log": ["[w2] 完成"]}


def worker_w3(state, ctx):
    return {"artifacts": ["w3 产物"], "log": ["[w3] 完成"]}


def join(state, ctx):
    return {"log": [f"[join] 汇聚 {len(state['artifacts'])} 个产物: {state['artifacts']}"]}


def dynamic_split(state, ctx):
    tasks = state.get("tasks") or [
        {"id": "api", "title": "实现 API"},
        {"id": "tests", "title": "补测试"},
        {"id": "docs", "title": "写文档"},
    ]
    return {
        "dynamic_tasks": tasks,
        "log": [f"[dynamic_split] 动态生成 {len(tasks)} 个 worker"],
    }


def route_dynamic_sends(state):
    return [
        Send("dynamic_worker", {"task": task}, key=task["id"])
        for task in state.get("dynamic_tasks", [])
    ]


def dynamic_worker(state, ctx):
    task = state["task"]
    artifact = f"{task['id']}:{task['title']}"
    return {
        "fanout": {ctx.instance_id: artifact},
        "log": [f"[dynamic_worker] {artifact}"],
    }


def dynamic_join(state, ctx):
    values = sorted(state.get("fanout", {}).values())
    return {"log": [f"[dynamic_join] 汇聚 {len(values)} 个动态产物: {values}"]}


def flaky(state, ctx):
    # 前两次 attempt 抛错，第三次成功
    if ctx.attempt < 2:
        raise RuntimeError(f"第 {ctx.attempt} 次尝试失败（模拟瞬时错误）")
    return {"log": [f"[flaky] 第 {ctx.attempt} 次尝试成功"]}


def dummy_coder_fix_test(state, ctx):
    v = state.get("code_version", 0) + 1
    workdir = state.get("workdir", "")
    if workdir:
        # 修复测试文件：把 assert fib(5) == 99 改成 assert fib(5) == 5
        test_file = os.path.join(workdir, "src", "test_fib.py")
        if os.path.isfile(test_file):
            with open(test_file, "r", encoding="utf-8") as f:
                content = f.read()
            content = content.replace("assert fib(5) == 99", "assert fib(5) == 5")
            with open(test_file, "w", encoding="utf-8") as f:
                f.write(content)
    return {"code_version": v, "log": [f"[Coder] 修复 v{v}"]}


def route_after_debug_to_end(state):
    return END if state.get("tests_passed") else "coder"


def demo_node_registry():
    return {
        "planner": planner,
        "coder": coder,
        "debugger": debugger,
        "ai_review": ai_review,
        "human_review": human_review,
        "split": split,
        "w1": worker_w1,
        "w2": worker_w2,
        "w3": worker_w3,
        "join": join,
        "dynamic_split": dynamic_split,
        "dynamic_worker": dynamic_worker,
        "dynamic_join": dynamic_join,
        "flaky": flaky,
        "dummy_coder_fix_test": dummy_coder_fix_test,
    }


def demo_router_registry():
    return {
        "route_after_debug": route_after_debug,
        "route_after_human_review": route_after_human_review,
        "route_after_debug_to_end": route_after_debug_to_end,
        "route_dynamic_sends": route_dynamic_sends,
    }


def build_configured_graph(graph_name: str, checkpointer: Checkpointer):
    return build_graph_from_config(
        load_graph_config(CONFIG_PATH),
        graph_name,
        demo_node_registry(),
        demo_router_registry(),
        checkpointer,
    )


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(f"  {title}")
    print("=" * 64)
