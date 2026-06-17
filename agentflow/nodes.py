"""AgentMesh 式四节点：Planner / Coder / Debugger / Reviewer。

对应报告第四章「形态 A：流水线式（AgentMesh）」。

每个节点是普通函数 (state, ctx) -> partial update。LLM 接入点已抽到配置层
（见 llm.py）：节点通过 get_registry().complete("<节点名>", prompt) 调用，
具体走 Claude / OpenAI / mock 由 llm_config.json 中该节点的配置决定。

无配置文件时所有节点走 mock，demo 与测试可离线、确定性运行——为此节点的
**控制流**（任务拆分、版本递增、测试通过条件）仍由 state 推导，不依赖 LLM 输出，
LLM 仅用于产出「内容」（计划文本 / 代码 / 评审意见），保证回环可复现。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .graph import NodeContext
from .llm import LLMRegistry

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
    """需求分析 + 任务分解。"""
    requirement = state["requirement"]
    # 控制流：确定性拆分（保证 demo 可复现）
    raw = requirement.replace("，", ",").replace("；", ";").replace(";", ",")
    tasks = [t.strip() for t in raw.split(",") if t.strip()] or [f"实现：{requirement}"]
    # 内容：交给该节点配置的 LLM 产出一段计划说明
    try:
        plan = get_registry().complete(
            "planner",
            f"将以下需求做结构化分解并简述实现计划：\n{requirement}",
            system="你是资深需求分析师，输出简洁的任务计划。",
        )
    except Exception:
        plan = "[LLM 不可用，使用确定性拆分]"
    return {
        "tasks": tasks,
        "plan": plan,
        "log": [f"[Planner] 分解为 {len(tasks)} 个子任务: {tasks}"],
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
        code = get_registry().complete(
            "coder", prompt, system="你是高级工程师，只输出代码。"
        )
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
        report = get_registry().complete(
            "debugger",
            f"对 v{version} 代码做测试评估，是否通过：{passed}",
            system="你是测试工程师，输出简短测试结论。",
        )
    except Exception:
        report = "[LLM 不可用，使用确定性测试结论]"
    return {
        "tests_passed": passed,
        "test_failures": failures,
        "test_report": report,
        "log": [f"[Debugger] 测试 v{version}: {'通过' if passed else '失败 → 退回 Coder'}"],
    }


def reviewer(state: Dict[str, Any], ctx: NodeContext) -> Dict[str, Any]:
    """最终评审：人在回路。先让 LLM 给评审意见，再请求人工决定。"""
    try:
        opinion = get_registry().complete(
            "reviewer",
            f"评审 v{state['code_version']} 代码，给出合并建议。",
            system="你是技术评审专家，输出 review 意见。",
        )
    except Exception:
        opinion = "[LLM 不可用，跳过 AI 评审意见]"
    decision = ctx.interrupt({
        "ask": "请评审并决定是否合并",
        "code_version": state["code_version"],
        "tasks": state["tasks"],
        "ai_opinion": opinion,
    })
    approved = bool(decision.get("approve")) if isinstance(decision, dict) else bool(decision)
    return {
        "approved": approved,
        "review_note": decision if isinstance(decision, dict) else {"approve": approved},
        "log": [f"[Reviewer] 人工决定: {'合并' if approved else '打回'}"],
    }


# —— 条件边路由函数 —— #

def route_after_debug(state: Dict[str, Any]) -> str:
    """测试通过 → 进入评审；否则 → 退回 Coder（形成回环）。"""
    return "reviewer" if state.get("tests_passed") else "coder"


def route_after_review(state: Dict[str, Any]) -> str:
    from .graph import END
    return END if state.get("approved") else "coder"
