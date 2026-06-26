"""P1-2 结构化 Planner 测试（对应 plan-p1.md 列出的 7 个用例 + 兼容性用例）。"""

import json
import sys
import os

# 确保 PYTHONPATH=. 时可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentflow import (
    Checkpointer,
    Command,
    Plan,
    StateGraph,
    StateSchema,
    START,
    END,
    parse_plan_from_llm,
)
from agentflow.graph import NodeContext
from agentflow.nodes import planner, set_registry
from agentflow.llm import LLMRegistry


# —— 用例 1：合法 Plan（含空 acceptance / 空 clarifying_questions）—— #
def test_plan_valid_empty_acceptance():
    p = Plan(
        summary="做个登录功能",
        tasks=[{"id": "t1", "title": "登录实现", "details": "用 JWT"}],
        acceptance_criteria=[],
        clarifying_questions=[],
    )
    errs = p.validate()
    assert errs == [], f"合法 Plan 不应报错，得到 {errs}"
    print("✅ test_plan_valid_empty_acceptance 通过")


# —— 用例 2：空 tasks → validate 返回错误 —— #
def test_plan_invalid_empty_tasks():
    p = Plan(summary="x", tasks=[], acceptance_criteria=[], clarifying_questions=[])
    errs = p.validate()
    assert any("tasks" in e for e in errs), f"应提示 tasks 至少 1 个，得到 {errs}"
    # summary 也为空时会一并报错（这是允许的）
    p2 = Plan(summary="有 summary", tasks=[])
    errs2 = p2.validate()
    assert any("tasks" in e for e in errs2), errs2
    print("✅ test_plan_invalid_empty_tasks 通过")


def test_plan_invalid_task_missing_id_title():
    p = Plan(summary="x", tasks=[{"details": "no id/title"}])
    errs = p.validate()
    assert any("id" in e for e in errs)
    assert any("title" in e for e in errs)
    print("✅ test_plan_invalid_task_missing_id_title 通过")


# —— 用例 3：合法 JSON 字符串 → parse_plan_from_llm 返回正确 Plan —— #
def test_parse_pure_json():
    raw = json.dumps({
        "summary": "实现用户登录",
        "tasks": [
            {"id": "t1", "title": "登录 API", "details": "POST /login"},
            {"id": "t2", "title": "JWT 中间件", "details": "verify token"},
        ],
        "acceptance_criteria": ["覆盖 80%", "无安全漏洞"],
        "clarifying_questions": ["是否需要 OAuth?"],
    }, ensure_ascii=False)
    plan = parse_plan_from_llm(raw, "实现用户登录")
    errs = plan.validate()
    assert errs == [], errs
    assert plan.summary == "实现用户登录"
    assert len(plan.tasks) == 2
    assert plan.tasks[0]["id"] == "t1"
    assert plan.tasks[1]["title"] == "JWT 中间件"
    assert plan.acceptance_criteria == ["覆盖 80%", "无安全漏洞"]
    assert plan.clarifying_questions == ["是否需要 OAuth?"]
    # round-trip
    d = plan.to_dict()
    p2 = Plan.from_dict(d)
    assert p2.to_dict() == plan.to_dict()
    print("✅ test_parse_pure_json 通过")


# —— 用例 4：含 ```json ... ``` 代码块的字符串 → 提取块后解析 —— #
def test_parse_json_in_code_block():
    raw = (
        "好的，以下是结构化计划：\n"
        "```json\n"
        + json.dumps({
            "summary": "做一个 todo",
            "tasks": [{"id": "t1", "title": "todo list", "details": "CRUD"}],
            "acceptance_criteria": ["增删改查正常"],
            "clarifying_questions": [],
        }, ensure_ascii=False)
        + "\n```\n"
        "请确认。"
    )
    plan = parse_plan_from_llm(raw, "做一个 todo")
    errs = plan.validate()
    assert errs == [], errs
    assert plan.summary == "做一个 todo"
    assert plan.tasks[0]["title"] == "todo list"
    assert plan.acceptance_criteria == ["增删改查正常"]
    print("✅ test_parse_json_in_code_block 通过")


# —— 用例 5：完全无法解析的字符串 → fallback 生成确定性 Plan —— #
def test_parse_garbage_falls_back_to_mock():
    raw = "这是一段完全不是 JSON 的废话，前后还包了 #@$%^&*"
    plan = parse_plan_from_llm(raw, "原始需求：X")
    # 必须返回合法 Plan
    errs = plan.validate()
    assert errs == [], f"fallback Plan 应合法，得到 {errs}"
    # 1 个 task，title = requirement
    assert len(plan.tasks) == 1
    assert plan.tasks[0]["title"] == "原始需求：X"
    assert "原始需求" in plan.summary
    print("✅ test_parse_garbage_falls_back_to_mock 通过")


def test_parse_empty_string_falls_back_to_mock():
    """LLM 异常 / 空字符串也应走 fallback。"""
    plan = parse_plan_from_llm("", "需求 ABC")
    errs = plan.validate()
    assert errs == [], errs
    assert plan.tasks[0]["title"] == "需求 ABC"
    print("✅ test_parse_empty_string_falls_back_to_mock 通过")


# —— 用例 6：planner 节点返回的 state["plan"] 是 dict，state["tasks"] 是 id 列表 —— #
def test_planner_node_returns_structured_state():
    # 重写 mock 行为：让 LLM 直接返回结构化 JSON
    reg = LLMRegistry(
        providers={
            "mock": {
                "base_url": "", "api_key_env": "", "model": "mock",
                "protocol": "mock",
            },
        },
        nodes={"planner": {"provider": "mock"}},
    )
    set_registry(reg)

    # 用一个最简单的 StateGraph 装一个 planner 节点
    g = StateGraph(StateSchema())
    g.add_node("planner", planner)
    g.add_edge(START, "planner")
    g.add_edge("planner", END)
    app = g.compile(Checkpointer())
    res = app.invoke({"requirement": "实现登录，加上单元测试"}, thread_id="t-planner-1")
    assert res.status == "completed", res.status

    plan = res.state["plan"]
    tasks = res.state["tasks"]
    assert isinstance(plan, dict), f"state['plan'] 应为 dict，得到 {type(plan).__name__}"
    assert "summary" in plan and "tasks" in plan
    assert "acceptance_criteria" in plan and "clarifying_questions" in plan
    assert isinstance(tasks, list)
    assert all(isinstance(t, str) for t in tasks), f"tasks 应为 id 列表，得到 {repr(tasks)}"
    # 与 plan.tasks 的 id 一一对应
    assert tasks == [t["id"] for t in plan["tasks"]]
    print("✅ test_planner_node_returns_structured_state 通过:", tasks)


def test_planner_node_falls_back_when_llm_returns_garbage():
    """planner 节点在 LLM 返回乱码时仍能产出合法 Plan。"""
    # 注册一个返回乱码的「假 LLM」
    class GarbageRegistry(LLMRegistry):
        def complete(self, node, prompt, *, system_prompt=None):
            return "今天天气真好，不输出 JSON"
    set_registry(GarbageRegistry())

    g = StateGraph(StateSchema())
    g.add_node("planner", planner)
    g.add_edge(START, "planner")
    g.add_edge("planner", END)
    app = g.compile(Checkpointer())
    res = app.invoke({"requirement": "做个 todo"}, thread_id="t-planner-2")
    assert res.status == "completed", res.status
    assert isinstance(res.state["plan"], dict)
    # 确定性 fallback：1 个 task，title=requirement
    assert len(res.state["plan"]["tasks"]) == 1
    assert res.state["plan"]["tasks"][0]["title"] == "做个 todo"
    assert res.state["tasks"] == ["t1"]
    print("✅ test_planner_node_falls_back_when_llm_returns_garbage 通过")


# —— 用例 7：mock LLM 下 demo 仍能跑通（fallback 路径）—— #
def test_planner_works_with_mock_llm():
    """与 demo.py 相同的配置：默认 registry 是 mock LLM。"""
    from agentflow.nodes import get_registry
    # 重置 module-level registry，确保走「找不到配置文件」的全 mock 路径
    import agentflow.nodes as nodes_mod
    nodes_mod._registry = None
    # 将配置文件指向不存在的路径，确保全 mock
    import os
    old_env = os.environ.get("AGENTFLOW_LLM_CONFIG")
    os.environ["AGENTFLOW_LLM_CONFIG"] = "/tmp/nonexistent_llm_config.json"
    try:
        reg = get_registry()
        cfg = reg.config_for("planner")
        assert cfg.provider == "mock", f"无配置时 planner 应走 mock，得到 {cfg.provider}"
    finally:
        if old_env is None:
            del os.environ["AGENTFLOW_LLM_CONFIG"]
        else:
            os.environ["AGENTFLOW_LLM_CONFIG"] = old_env
        nodes_mod._registry = None  # 不影响后续用例

    g = StateGraph(StateSchema())
    g.add_node("planner", planner)
    g.add_edge(START, "planner")
    g.add_edge("planner", END)
    app = g.compile(Checkpointer())
    res = app.invoke({"requirement": "做一个 hello world"}, thread_id="t-mock")
    assert res.status == "completed"
    plan = res.state["plan"]
    assert isinstance(plan, dict)
    # mock 输出的字符串通常无法直接 JSON 解析，会走 fallback（除非 LLM 偶然产出 JSON）
    assert isinstance(plan["tasks"], list) and len(plan["tasks"]) >= 1
    print("✅ test_planner_works_with_mock_llm 通过")


def test_full_pipeline_demo_compatible():
    """与 demo.py scenario_pipeline 行为兼容：plan=dict / tasks=id 列表，planner 之后各节点能跑通。"""
    from agentflow.nodes import coder, debugger, ai_review, human_review, route_after_debug, route_after_human_review

    schema = StateSchema()
    g = StateGraph(schema, max_steps=30)
    g.add_node("planner", planner)
    g.add_node("coder", coder)
    g.add_node("debugger", debugger)
    g.add_node("ai_review", ai_review)
    g.add_node("human_review", human_review)
    g.add_edge(START, "planner")
    g.add_edge("planner", "coder")
    g.add_edge("coder", "debugger")
    g.add_conditional_edges("debugger", route_after_debug)
    g.add_edge("ai_review", "human_review")
    g.add_conditional_edges("human_review", route_after_human_review)
    app = g.compile(Checkpointer())
    res = app.invoke(
        {"requirement": "实现登录接口，加上单元测试，写好文档", "pass_at_version": 3},
        thread_id="t-pipe",
    )
    # 应在 Reviewer 中断
    assert res.status == "interrupted", res.status
    assert isinstance(res.state["plan"], dict)
    assert "summary" in res.state["plan"]
    # 终止 pipeline
    final = app.invoke({}, thread_id="t-pipe", command=Command(resume={"approve": True}))
    assert final.status == "completed", final.status
    print("✅ test_full_pipeline_demo_compatible 通过")


if __name__ == "__main__":
    test_plan_valid_empty_acceptance()
    test_plan_invalid_empty_tasks()
    test_plan_invalid_task_missing_id_title()
    test_parse_pure_json()
    test_parse_json_in_code_block()
    test_parse_garbage_falls_back_to_mock()
    test_parse_empty_string_falls_back_to_mock()
    test_planner_node_returns_structured_state()
    test_planner_node_falls_back_when_llm_returns_garbage()
    test_planner_works_with_mock_llm()
    test_full_pipeline_demo_compatible()
    print("\n✅ 全部测试通过\n")