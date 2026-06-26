"""AgentMesh 式节点：Planner / Coder / Debugger / AI Review / Human Review。

对应报告第四章「形态 A：流水线式（AgentMesh）」。

每个节点是普通函数 (state, ctx) -> partial update。LLM 接入点已抽到配置层
（见 llm.py）：节点通过 get_registry().complete("<节点名>", prompt) 调用，
具体走 Claude / OpenAI / mock 由 llm_config.json 中该节点的配置决定。

无配置文件时所有节点走 mock，demo 与测试可离线、确定性运行——为此节点的
**控制流**（任务拆分、版本递增、测试通过条件）仍由 state 推导，不依赖 LLM 输出，
LLM 仅用于产出「内容」（计划文本 / 代码 / 评审意见），保证回环可复现。
"""

import json
import os
import tempfile
import warnings
from typing import Any

from .graph import NodeContext
from .llm import LLMRegistry
from .plan import Plan, parse_plan_from_llm

# —— 模块级 registry：懒加载，可被 set_registry 覆盖 —— #
_registry: LLMRegistry | None = None


def get_registry() -> LLMRegistry:
    global _registry
    if _registry is None:
        _registry = LLMRegistry.load()  # 找不到配置文件 → 全 mock
    return _registry


def set_registry(reg: LLMRegistry) -> None:
    """供 demo / 测试注入自定义配置。"""
    global _registry
    _registry = reg


def planner(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
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
    tasks_seed: list[dict] = [
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
                "planner", prompt, system_prompt="你是资深需求分析师，输出严格的 JSON。"
            ),
            input_summary=requirement,
        )
    except Exception:
        llm_text = ""
    # 三层 fallback：直接 JSON → ```json``` 代码块 → 优先 tasks_seed / mock
    plan = parse_plan_from_llm(llm_text, requirement, tasks_seed=tasks_seed)
    # 兜底：若 LLM 输出解析后 task 为空，优先用种子 task（保留确定性拆分）
    if not plan.tasks:
        plan.tasks = tasks_seed
    # 若种子也没有（requirement 本身是空串？），最终兜底
    if not plan.tasks:
        plan.tasks = [{"id": "t1", "title": requirement, "details": requirement}]
    if not plan.summary or not plan.summary.strip():
        plan.summary = f"实现 {requirement}"
    # 兜底：保证每个 task 至少有 id+title 两个字段
    existing_ids = {t.get("id") for t in plan.tasks if t.get("id")}
    counter = 0
    for t in plan.tasks:
        if "id" not in t:
            # 自动分配一个不与已有 id 冲突的 id
            while True:
                counter += 1
                candidate = f"t{counter}"
                if candidate not in existing_ids:
                    break
            t["id"] = candidate
            existing_ids.add(candidate)
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


def coder(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    """真实 Coder：遍历 plan.tasks，对每个 task 调 LLM 生成代码并写入文件。

    使用 ctx.tool("write_file", key=task_id, ...) 写入，自动获得缓存 + 审计。
    LLM 失败时写 stub 文件（避免中断 pipeline）。
    """
    version = state.get("code_version", 0) + 1
    plan_dict = state.get("plan", {})
    plan_tasks = plan_dict.get("tasks", []) if isinstance(plan_dict, dict) else []
    # 显式处理 None（当 key 存在但值为 None 时不依赖 falsy 巧合）
    if plan_tasks is None:
        plan_tasks = []

    # 兼容旧场景（无 plan.tasks）：从 state["tasks"] 取 id 列表
    legacy_tasks = state.get("tasks", [])
    if not plan_tasks and legacy_tasks:
        # 旧场景：tasks 是字符串列表 ["t1", "t2", ...] 或含 id/title 的 dict 列表
        if isinstance(legacy_tasks[0], str):
            plan_tasks = [{"id": tid, "title": tid, "details": tid} for tid in legacy_tasks]
        elif isinstance(legacy_tasks[0], dict):
            plan_tasks = legacy_tasks

    feedback = state.get("test_failures") or []

    # 获取或创建 workdir
    workdir = state.get("workdir") if (workdir_explicit := "workdir" in state) else tempfile.mkdtemp(prefix="af-coder-")

    # CR 2026-06-18 1.1: 收集已有 id，避免自动分配时冲突
    existing_ids = {t.get("id") for t in plan_tasks if t.get("id")}

    artifacts = []
    for i, task in enumerate(plan_tasks):
        task_id = task.get("id")
        if not task_id:
            # 自动分配一个不冲突的 id
            candidate = f"t{i+1}"
            while candidate in existing_ids:
                i += 1
                candidate = f"t{i+1}"
            task_id = candidate
            existing_ids.add(task_id)
            warnings.warn(f"[Coder] task #{i+1} 缺 id，自动分配为 '{task_id}'")
        task_title = task.get("title", "")
        task_details = task.get("details", task_title)

        # 文件路径：{workdir}/src/task_{id}.py
        file_dir = os.path.join(workdir, "src")
        os.makedirs(file_dir, exist_ok=True)
        # 确保 __init__.py 存在，使 pytest 能 import src
        init_path = os.path.join(file_dir, "__init__.py")
        if not os.path.exists(init_path):
            with open(init_path, "w") as f:
                f.write("# auto-generated package\n")
        file_path = os.path.join(file_dir, f"task_{task_id}.py")

        # 构造 prompt
        prompt = f"为任务 {task_id}「{task_title}」编写实现代码。\n详情：{task_details}"
        if feedback:
            prompt += f"\n上一版测试失败，请修复：{feedback}"

        # LLM 调用（走 activity 缓存）
        try:
            code = ctx.activity(
                f"llm_code_{task_id}",
                lambda: get_registry().complete(
                    "coder", prompt, system_prompt="你是高级工程师，只输出代码。同时请生成 pytest 测试文件（test_*.py）。"
                ),
                input_summary=task_title,
            )
        except Exception:
            code = f"# {task_title}\n# mock code (LLM 不可用)\n"

        # 写文件（走 ctx.tool 审计 + 独立缓存 key=task_id）
        ctx.tool(
            "write_file", key=task_id,
            fn=lambda p=file_path, c=code: (
                open(p, "w", encoding="utf-8").write(c),
                {"path": p, "bytes": len(c.encode("utf-8"))},
            )[1],
            input_summary=task_title,
        )

        artifacts.append(f"src/task_{task_id}.py")

        # 为每个 task 生成 pytest 测试文件
        test_file_path = os.path.join(file_dir, f"test_task_{task_id}.py")
        try:
            test_code = ctx.activity(
                f"llm_test_{task_id}",
                lambda: get_registry().complete(
                    "coder", f"为任务 {task_id}「{task_title}」编写 pytest 测试文件。\n实现代码：\n{code}",
                    system_prompt="你是高级工程师，只输出 pytest 测试代码。",
                ),
                input_summary=f"test_{task_id}",
            )
        except Exception:
            test_code = f"# test for {task_id}\n# mock test (LLM 不可用)\n"
        if test_code.strip():
            ctx.tool(
                "write_file", key=f"test_{task_id}",
                fn=lambda p=test_file_path, c=test_code: (
                    open(p, "w", encoding="utf-8").write(c),
                    {"path": p, "bytes": len(c.encode("utf-8"))},
                )[1],
                input_summary=f"test_{task_id}",
            )
            artifacts.append(f"src/test_task_{task_id}.py")

    result: dict[str, Any] = {
        "code": f"// v{version} — 共 {len(plan_tasks)} 个文件",
        "code_version": version,
        "log": [f"[Coder] 产出代码 v{version}，{len(plan_tasks)} 个文件: {artifacts}"],
    }
    # 仅在显式传入 workdir 时（真实 Coder 模式）才写 artifacts/workdir 到 state，
    # 避免干扰 P1-4 Debugger 的 pass_at_version 兜底路径（兼容 scenario 1-5）。
    if workdir_explicit:
        result["artifacts"] = artifacts
        result["workdir"] = workdir
    else:
        # CR Backlog 2026-06-18 2.3: 自建的临时 workdir 用后即清理
        import shutil
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass
    return result


def debugger(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    """真实 Debugger：跑 pytest，解析结果，写结构化 test_failures。

    兜底策略：
    - 无测试文件 → tests_passed=True，log 提示"未发现测试"
    - pytest 命令失败（非零退出）→ 解析 FAILED 行
    - LLM 总结仅在 failures 非空时调
    """
    version = state["code_version"]
    workdir = state.get("workdir", "")
    artifacts = state.get("artifacts", [])
    import re
    import os as _os

    def _fallback(passed: bool, failures: list, reason: str) -> dict[str, Any]:
        """公共 fallback：LLM 总结 + 组装返回值。"""
        try:
            report = ctx.activity("llm_complete", lambda: get_registry().complete(
                "debugger",
                f"{reason}，对 v{version} 代码做测试评估，是否通过：{passed}",
                system_prompt="你是测试工程师，输出简短测试结论。",
            ))
        except Exception:
            report = "[LLM 不可用，使用确定性测试结论]"
        return {
            "tests_passed": passed,
            "test_failures": failures,
            "test_report": report,
            "log": [f"[Debugger] {reason}，测试 v{version}: {'通过' if passed else '失败 → 退回 Coder'}"],
        }

    # 无 workdir：fallback 到旧行为（兼容 scenario 1-5）
    if not workdir or not artifacts:
        passed = version >= (state.get("pass_at_version") or 3)
        failures = [] if passed else [f"子任务 {t} 的用例未通过" for t in state["tasks"][:1]]
        return _fallback(passed, failures, "无 workdir")

    # 发现测试文件：**/test_*.py + **/*_test.py（相对于 workdir）
    test_files = []
    for root, dirs, files in _os.walk(workdir):
        for f in files:
            if f.startswith("test_") and f.endswith(".py"):
                test_files.append(_os.path.relpath(_os.path.join(root, f), workdir))
            elif f.endswith("_test.py"):
                test_files.append(_os.path.relpath(_os.path.join(root, f), workdir))

    if not test_files:
        # 兜底：无测试文件 = 默认通过
        return {
            "tests_passed": True,
            "test_failures": [],
            "test_report": "[Debugger] 未发现测试文件，默认通过",
            "log": [f"[Debugger] v{version}: 未发现测试文件，默认通过"],
        }

    # 跑 pytest
    import shlex
    import subprocess as _subprocess
    import time as _time

    # 探测 pytest 是否可用
    try:
        _probe = _subprocess.run(
            ["pytest", "--version"], capture_output=True, text=True, timeout=5,
        )
        _pytest_available = _probe.returncode == 0
    except (FileNotFoundError, _subprocess.TimeoutExpired):
        _pytest_available = False

    if not _pytest_available:
        passed = version >= (state.get("pass_at_version") or 3)
        failures = [] if passed else [
            {"test_name": "pytest_unavailable",
             "error_msg": "pytest 不可用，使用 pass_at_version 判定"}
        ]
        return _fallback(passed, failures, "pytest 不可用")
    test_files_arg = " ".join(shlex.quote(path) for path in test_files)
    cmd = f"pytest {test_files_arg} --tb=short -q"

    # CR 2026-06-17 1.2: 虽然不用 ToolRuntime.run_cmd，但手动复用其安全检查
    from .tools import _check_no_dotdot
    _check_no_dotdot(cmd)

    def _run_pytest():
        t0 = _time.time()
        try:
            proc = _subprocess.run(
                cmd, shell=True, cwd=workdir,
                capture_output=True, text=True, timeout=120,
            )
        except _subprocess.TimeoutExpired:
            return {
                "exit_code": -1, "stdout": "", "stderr": "timeout",
                "duration_ms": 120000, "error": "timeout",
            }
        duration_ms = (_time.time() - t0) * 1000
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "duration_ms": duration_ms,
        }

    try:
        result = ctx.tool("run_cmd", key=f"pytest_v{version}",
                         fn=_run_pytest,
                         input_summary=f"pytest v{version}")
    except Exception as e:
        # run_cmd 本身失败（超时/PermissionError 等）
        return {
            "tests_passed": False,
            "test_failures": [{"test_name": "run_cmd", "error_msg": str(e)}],
            "test_report": f"[Debugger] pytest 执行失败: {e}",
            "log": [f"[Debugger] v{version}: pytest 执行异常 → 退回 Coder"],
        }

    exit_code = result.get("exit_code", -1)
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")

    # 解析 pytest 输出
    tests_passed = exit_code == 0
    failures = []
    if not tests_passed:
        # 提取 FAILED 行：两种格式都兼容
        # 格式 1: FAILED path::test_name - error_msg
        # 格式 2: FAILED path::test_name (无错误信息)
        for line in (stdout + "\n" + stderr).splitlines():
            # CR 2026-06-17 1.1: 支持 TestClass::test_method 多级 :: 和 parametrized test
            m = re.match(r"FAILED\s+(\S+(?:::\S+)*)\s*[-:]\s*(.*)", line)
            if m:
                failures.append({"test_name": m.group(1), "error_msg": m.group(2).strip()})
                continue
            # 兜底：匹配没有错误消息的 FAILED 行 + 支持前导空格（CR 2026-06-17 3.3）
            m2 = re.search(r"FAILED\s+(\S+(?:::\S+)*)", line)
            if m2:
                failures.append({"test_name": m2.group(1), "error_msg": "assertion failed"})
        # CR 2026-06-17 2.1: pytest 收集失败（语法错误等）或 pytest 不可用
        # exit_code != 0 但没有 FAILED 行 → 用 stderr 摘要作为 fallback
        if not failures:
            summary = (stderr or stdout or "").strip()
            if not summary:
                summary = f"pytest exit_code={exit_code}（无可用错误信息）"
            failures.append({"test_name": "pytest_collection", "error_msg": summary[:500]})

    # LLM 总结（仅在 failures 非空时调）
    report = ""
    if failures:
        try:
            report = ctx.activity(
                f"llm_debug_{version}",
                lambda: get_registry().complete(
                    "debugger",
                    f"pytest v{version} 输出：\n{stdout[:2000]}\n\n"
                    f"请总结失败原因并给出修复建议。",
                    system_prompt="你是测试工程师，输出简短诊断。",
                ),
            )
        except Exception:
            report = f"[LLM 不可用] 测试 v{version} 失败: {len(failures)} 个用例"
    else:
        report = f"[Debugger] 测试 v{version} 全部通过"

    return {
        "tests_passed": tests_passed,
        "test_failures": failures,
        "test_report": report,
        "log": [f"[Debugger] 测试 v{version}: {'通过' if tests_passed else f'失败({len(failures)}个) → 退回 Coder'}"],
    }


def ai_review(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    """AI 评审：纯 LLM 评审，输出技术意见。不中断。"""
    try:
        comments = ctx.activity("ai_review_llm", lambda: get_registry().complete(
            "reviewer",
            f"评审 v{state['code_version']} 代码（tasks: {state['tasks']}），"
            f"重点关注：代码质量、边界处理、测试覆盖。",
            system_prompt="你是技术评审专家，给出结构化 review 意见。",
        ))
    except Exception:
        comments = "[LLM 不可用，跳过 AI 评审意见]"
    return {
        "ai_review": comments,
        "log": [f"[AI Reviewer] 完成 v{state['code_version']} 评审"],
    }


def human_review(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    """人在回路：基于 AI 评审意见决定合并/打回。中断等待人工输入。

    resume 值可以是：
    - True / False (bare bool)：True=合并，False=打回
    - {"approve": True/False} (dict)：同上
    - 其他 truthy/falsy 值：按 bool() 判定
    """
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

def route_after_debug(state: dict[str, Any]) -> str:
    """测试通过 → 进入 AI 评审；否则 → 退回 Coder（形成回环）。"""
    return "ai_review" if state.get("tests_passed") else "coder"


def route_after_human_review(state: dict[str, Any]) -> str:
    from .graph import END
    return END if state.get("approved") else "coder"
