"""AgentMesh 式节点：Planner / Coder / Debugger / AI Review / Human Review。

对应报告第四章「形态 A：流水线式（AgentMesh）」。

每个节点是普通函数 (state, ctx) -> partial update。LLM 接入点已抽到配置层
（见 llm.py）：节点通过 get_registry().complete("<节点名>", prompt) 调用，
具体走 Claude / OpenAI / mock 由 llm_config.json 中该节点的配置决定。

无配置文件时所有节点走 mock，demo 与测试可离线、确定性运行——为此节点的
**控制流**（任务拆分、版本递增、测试通过条件）仍由 state 推导，不依赖 LLM 输出，
LLM 仅用于产出「内容」（计划文本 / 代码 / 评审意见），保证回环可复现。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .graph import NodeContext
from .llm import LLMRegistry
from .plan import Plan, parse_plan_from_llm

# —— 模块级 registry：懒加载，可被 set_registry 覆盖 —— #
_registry: Optional[LLMRegistry] = None


def get_registry() -> LLMRegistry:
    global _registry
    if _registry is None:
        _registry = LLMRegistry.load()  # 找不到配置文件 → 全 mock
    return _registry


def set_registry(reg: LLMRegistry) -> None:
    """供 demo / 测试注入自定义配置。"""
    global _registry
    _registry = reg


def planner(state: Dict[str, Any], ctx: NodeContext) -> Dict[str, Any]:
    """需求分析 + 任务分解（P1-2 结构化版）。

    产出结构化 Plan（dict）写入 state["plan"]，同时把 plan.tasks 的 id 列表
    写入 state["tasks"] 兼容下游节点。控制流（任务拆分、id 分配）保持
    确定性，LLM 只产出「内容」，保证 demo 可复现。
    """
    requirement = state["requirement"]
    # 控制流：确定性按逗号 split 出 task titles（中文逗号 / 分号也兼容）
    raw = requirement.replace("，", ",").replace("；", ";").replace(";", ",")
    task_titles = [t.strip() for t in raw.split(",") if t.strip()] or [f"实现：{requirement}"]
    # 任务 id 分配：t1, t2, t3...
    tasks_seed: List[Dict] = [
        {"id": f"t{i+1}", "title": title, "details": title}
        for i, title in enumerate(task_titles)
    ]
    # 内容：让 LLM 以严格 JSON 格式产出 Plan
    schema_hint = {
        "summary": "1-2 句话总结",
        "tasks": [{"id": "t1", "title": "...", "details": "..."}],
        "acceptance_criteria": ["..."],
        "clarifying_questions": ["..."],
    }
    prompt = (
        "分析以下需求并以 JSON 格式输出计划。\n"
        f"需求：{requirement}\n\n"
        f"严格按如下 schema 输出 JSON：\n{json.dumps(schema_hint, ensure_ascii=False, indent=2)}"
    )
    try:
        llm_text = ctx.activity(
            "llm_plan",
            lambda: get_registry().complete(
                "planner", prompt, system="你是资深需求分析师，输出严格的 JSON。"
            ),
            input_summary=requirement,
        )
    except Exception:
        llm_text = ""
    # 三层 fallback：直接 JSON → ```json``` 代码块 → 确定性 mock
    plan = parse_plan_from_llm(llm_text, requirement)
    # 兜底：若 LLM 输出解析后 task 为空，用种子 task 补；summary 为空则补默认
    if not plan.tasks:
        plan.tasks = tasks_seed
    if not plan.summary or not plan.summary.strip():
        plan.summary = f"实现 {requirement}"
    # 兜底：保证每个 task 至少有 id+title 两个字段
    for i, t in enumerate(plan.tasks):
        if "id" not in t:
            t["id"] = f"t{i+1}"
        if "title" not in t:
            t["title"] = requirement
        if "details" not in t:
            t["details"] = t.get("title", requirement)
    return {
        "plan": plan.to_dict(),                              # dict 形式存 state
        "tasks": [t["id"] for t in plan.tasks],              # 兼容下游（取 id 列表）
        "log": [
            f"[Planner] 产出 {len(plan.tasks)} 个子任务: {[t['id'] for t in plan.tasks]}"
        ],
    }


def coder(state: Dict[str, Any], ctx: NodeContext) -> Dict[str, Any]:
    """开发：为子任务产出代码。回环重入时递增版本。"""
    version = state.get("code_version", 0) + 1
    tasks = state["tasks"]
    feedback = state.get("test_failures") or []
    prompt = f"为以下子任务编写实现代码：{tasks}"
    if feedback:
        prompt += f"\n上一版测试失败，请修复：{feedback}"
    try:
        code = ctx.activity("llm_complete", lambda: get_registry().complete(
            "coder", prompt, system="你是高级工程师，只输出代码。"
        ))
    except Exception:
        code = "[LLM 不可用，使用默认实现]"
    return {
        "code": code,
        "code_version": version,
        "log": [f"[Coder] 产出代码 v{version}，覆盖 {len(tasks)} 个子任务"],
    }


def debugger(state: Dict[str, Any], ctx: NodeContext) -> Dict[str, Any]:
    """测试 + 纠错。控制流（是否通过）由 state 决定，保证回环可复现。"""
    version = state["code_version"]
    passed = version >= state.get("pass_at_version", 3)
    failures = [] if passed else [f"子任务 {t} 的用例未通过" for t in state["tasks"][:1]]
    # 内容：让 LLM 给一段测试报告（mock 时为确定性文本）
    try:
        report = ctx.activity("llm_complete", lambda: get_registry().complete(
            "debugger",
            f"对 v{version} 代码做测试评估，是否通过：{passed}",
            system="你是测试工程师，输出简短测试结论。",
        ))
    except Exception:
        report = "[LLM 不可用，使用确定性测试结论]"
    return {
        "tests_passed": passed,
        "test_failures": failures,
        "test_report": report,
        "log": [f"[Debugger] 测试 v{version}: {'通过' if passed else '失败 → 退回 Coder'}"],
    }


def ai_review(state: Dict[str, Any], ctx: NodeContext) -> Dict[str, Any]:
    """AI 评审：纯 LLM 评审，输出技术意见。不中断。"""
    try:
        comments = ctx.activity("ai_review_llm", lambda: get_registry().complete(
            "reviewer",
            f"评审 v{state['code_version']} 代码（tasks: {state['tasks']}），"
            f"重点关注：代码质量、边界处理、测试覆盖。",
            system="你是技术评审专家，给出结构化 review 意见。",
        ))
    except Exception:
        comments = "[LLM 不可用，跳过 AI 评审意见]"
    return {
        "ai_review": comments,
        "log": [f"[AI Reviewer] 完成 v{state['code_version']} 评审"],
    }


def human_review(state: Dict[str, Any], ctx: NodeContext) -> Dict[str, Any]:
    """人在回路：基于 AI 评审意见决定合并/打回。中断等待人工输入。"""
    decision = ctx.interrupt({
        "ask": "请评审并决定是否合并",
        "code_version": state["code_version"],
        "tasks": state["tasks"],
        "ai_review": state.get("ai_review", ""),
    })
    approved = bool(decision.get("approve")) if isinstance(decision, dict) else bool(decision)
    return {
        "approved": approved,
        "human_review_decision": decision if isinstance(decision, dict) else {"approve": approved},
        "log": [f"[Human Reviewer] 人工决定: {'合并' if approved else '打回'}"],
    }


# —— 条件边路由函数 —— #

def route_after_debug(state: Dict[str, Any]) -> str:
    """测试通过 → 进入 AI 评审；否则 → 退回 Coder（形成回环）。"""
    return "ai_review" if state.get("tests_passed") else "coder"


def route_after_human_review(state: Dict[str, Any]) -> str:
    from .graph import END
    return END if state.get("approved") else "coder"
