"""结构化 Plan 数据类 + LLM 输出三层 fallback 解析。

对应 P1-2 任务：把 planner 节点从「自由文本」升级为「结构化对象」。

设计要点：
- Plan 是 dataclass，便于 to_dict/from_dict/validate；
- parse_plan_from_llm 提供三层 fallback，保证 pipeline 永不因解析失败而中断；
- 不引入 logging 库（保持零依赖），警告信息用 print 输出。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Plan:
    """结构化的需求分析结果。"""

    summary: str = ""
    tasks: List[Dict] = field(default_factory=list)               # [{"id": "t1", "title": "...", "details": "..."}]
    acceptance_criteria: List[str] = field(default_factory=list)
    clarifying_questions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "summary": self.summary,
            "tasks": [dict(t) for t in self.tasks],   # 浅拷贝，避免下游意外改源
            "acceptance_criteria": list(self.acceptance_criteria),
            "clarifying_questions": list(self.clarifying_questions),
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "Plan":
        if not isinstance(d, dict):
            raise TypeError(f"Plan.from_dict 需要 dict，得到 {type(d).__name__}")
        tasks = d.get("tasks") or []
        # 防御：tasks 里混入非 dict 元素时直接丢弃，保持结构干净
        clean_tasks = [t for t in tasks if isinstance(t, dict)]
        return cls(
            summary=str(d.get("summary") or ""),
            tasks=clean_tasks,
            acceptance_criteria=[str(x) for x in (d.get("acceptance_criteria") or [])],
            clarifying_questions=[str(x) for x in (d.get("clarifying_questions") or [])],
        )

    def validate(self) -> List[str]:
        """返回错误信息列表。空列表 = 合法。"""
        errs: List[str] = []
        if not self.summary or not self.summary.strip():
            errs.append("summary 不能为空")
        if not self.tasks:
            errs.append("tasks 至少 1 个")
        for i, t in enumerate(self.tasks):
            if not isinstance(t, dict):
                errs.append(f"task[{i}] 不是 dict")
                continue
            if "id" not in t or not str(t.get("id", "")).strip():
                errs.append(f"task[{i}] 缺少 id")
            if "title" not in t or not str(t.get("title", "")).strip():
                errs.append(f"task[{i}] 缺少 title")
            if "details" in t and not isinstance(t["details"], str):
                errs.append(f"task[{i}].details 应为 str 类型")
        return errs


# —— 私有工具 —— #

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL | re.IGNORECASE)


def _coerce_to_plan(obj: object) -> Optional[Plan]:
    """把 json.loads 出来的对象规整成 Plan。返回 None 表示不可用。"""
    if obj is None:
        return None
    if isinstance(obj, list):
        # 接受 [{"id":..., "title":...}, ...] 这种「裸 task 列表」包装
        return Plan(tasks=[t for t in obj if isinstance(t, dict)])
    if isinstance(obj, dict):
        return Plan.from_dict(obj)
    return None


def _mock_plan(requirement: str) -> Plan:
    """确定性 fallback：单 task = requirement 本身。"""
    return Plan(
        summary=f"实现 {requirement}",
        tasks=[{"id": "t1", "title": requirement, "details": requirement}],
        acceptance_criteria=[],
        clarifying_questions=[],
    )


def parse_plan_from_llm(llm_text: str, requirement: str,
                        tasks_seed: Optional[List[Dict]] = None) -> Plan:
    """三层 fallback 解析 LLM 输出为 Plan。

    1. 直接 `json.loads(llm_text.strip())`
    2. 正则提取 ```json ... ```（或 ``` ... ```）代码块后再 json.loads
    3. 优先用 tasks_seed（保留确定性拆分），再兜底 _mock_plan

    每层失败都打 warning 日志（用 print，不用 logging 库）。
    最终一定返回合法 Plan（保证 pipeline 不会因解析失败而中断）。
    """
    text = (llm_text or "").strip()

    # 第 1 层：直接 JSON
    if text:
        try:
            obj = json.loads(text)
            plan = _coerce_to_plan(obj)
            if plan is not None and not plan.validate():
                return plan
            if plan is not None:
                print(f"[plan] WARN: 第 1 层直接 JSON 解析成功但 validate 失败: {plan.validate()}")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            print(f"[plan] WARN: 第 1 层直接 JSON 解析失败: {exc}")

    # 第 2 层：正则提取 ```json ... ```（或 ``` ... ```）代码块，依次尝试直到 validate 通过
    if text:
        found_any = False
        for m in _JSON_BLOCK_RE.finditer(text):
            found_any = True
            block = m.group(1).strip()
            try:
                obj = json.loads(block)
                plan = _coerce_to_plan(obj)
                if plan is not None and not plan.validate():
                    print(f"[plan] INFO: 第 2 层 JSON 块解析成功且 validate 通过")
                    return plan
                if plan is not None:
                    print(f"[plan] WARN: 第 2 层代码块 JSON validate 失败: {plan.validate()}")
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                print(f"[plan] WARN: 第 2 层代码块 JSON 解析失败: {exc}")
        if not found_any:
            print(f"[plan] WARN: 第 2 层未找到 ```json ... ``` 代码块")

    # 第 3 层：优先用 tasks_seed（保留确定性拆分），再兜底 _mock_plan
    print("[plan] WARN: 走第 3 层 fallback")
    if tasks_seed:
        plan = Plan(
            summary=f"实现 {requirement}",
            tasks=tasks_seed,
            acceptance_criteria=[],
            clarifying_questions=[],
        )
    else:
        plan = _mock_plan(requirement)
    # 尝试从 LLM 原文本中提取澄清问题（？ 结尾的句子）
    if text and not plan.clarifying_questions:
        questions = re.findall(r'([^。！？\n]*？)', text)
        if questions:
            plan.clarifying_questions = [q.strip() for q in questions[:5]]
    return plan
