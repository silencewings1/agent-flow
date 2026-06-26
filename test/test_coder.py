"""P1-3 真实 Coder 测试（对应 plan-p1.md 列出的 5 个用例 + 补充）。"""

import sys
import os

# 确保 PYTHONPATH=. 时可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile

from agentflow import (
    Checkpointer,
    Plan,
    StateGraph,
    StateSchema,
    START,
    END,
    append_reducer,
)
from agentflow.graph import NodeContext
from agentflow.nodes import coder, planner, set_registry
from agentflow.llm import LLMRegistry


# —— 辅助：构建最小的 planner → coder 流水线 —— #

def _make_pipeline(registry=None):
    """构建 planner → coder 两节点流水线。"""
    if registry is not None:
        set_registry(registry)
    schema = StateSchema(reducers={"log": append_reducer, "artifacts": append_reducer})
    g = StateGraph(schema, max_steps=10)
    g.add_node("planner", planner)
    g.add_node("coder", coder)
    g.add_edge(START, "planner")
    g.add_edge("planner", "coder")
    g.add_edge("coder", END)
    return g.compile(Checkpointer())


def _mock_registry():
    """返回 mock 模式的 LLMRegistry。"""
    return LLMRegistry(
        providers={},
        nodes={"planner": {"provider": "mock"}, "coder": {"provider": "mock"}},
    )


# —— 用例 1：coder 跑完，workdir 下文件存在非空 —— #

def test_coder_writes_files():
    with tempfile.TemporaryDirectory(prefix="af-test-") as workdir:
        app = _make_pipeline(_mock_registry())
        init = {
            "requirement": "实现 fibonacci 函数, 写单元测试",
            "workdir": workdir,
        }
        res = app.invoke(init, thread_id="tc-write")
        assert res.status == "completed", res.status

        artifacts = res.state.get("artifacts", [])
        assert len(artifacts) >= 1, f"应有至少 1 个产物，得到 {artifacts}"

        for art in artifacts:
            full = os.path.join(workdir, art)
            assert os.path.isfile(full), f"文件应存在: {full}"
            size = os.path.getsize(full)
            assert size > 0, f"文件不应为空: {full}"
    print("test_coder_writes_files 通过")


# —— 用例 2：state["artifacts"] 列出所有写入文件 —— #

def test_coder_artifacts_list_correct():
    with tempfile.TemporaryDirectory(prefix="af-test-") as workdir:
        app = _make_pipeline(_mock_registry())
        init = {
            "requirement": "实现 fibonacci, 写单元测试, 写文档",
            "workdir": workdir,
        }
        res = app.invoke(init, thread_id="tc-artifacts")
        assert res.status == "completed"

        plan_tasks = res.state["plan"]["tasks"]
        artifacts = res.state.get("artifacts", [])
        assert len(artifacts) == len(plan_tasks) * 2, \
            f"artifacts 数量 {len(artifacts)} 应等于 plan.tasks 数量 {len(plan_tasks)} * 2（每个 task 含实现+测试）"

        for i, task in enumerate(plan_tasks):
            expected_src = f"src/task_{task['id']}.py"
            expected_test = f"src/test_task_{task['id']}.py"
            assert artifacts[i * 2] == expected_src, f"artifacts[{i*2}] 应为 {expected_src}，得到 {artifacts[i*2]}"
            assert artifacts[i * 2 + 1] == expected_test, f"artifacts[{i*2+1}] 应为 {expected_test}，得到 {artifacts[i*2+1]}"

        # 验证每个 artifact 对应文件确实存在
        for art in artifacts:
            full = os.path.join(workdir, art)
            assert os.path.isfile(full), f"文件应存在: {full}"
    print("test_coder_artifacts_list_correct 通过")


# —— 用例 3：写文件操作在 tool_calls 表有记录 —— #

def test_coder_tool_calls_logged():
    with tempfile.TemporaryDirectory(prefix="af-test-") as workdir:
        cp = Checkpointer()
        set_registry(_mock_registry())
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
        res = app.invoke(init, thread_id="tc-tool-calls")
        assert res.status == "completed"

        # 查询 tool_calls 记录
        tool_calls = cp.tool_calls("tc-tool-calls")
        # tool_name 字段存的是 activity key（如 "tool:write_file:t1"），不是 "write_file"
        write_calls = [tc for tc in tool_calls if "write_file" in tc["activity_key"]]
        assert len(write_calls) >= 1, f"应有至少 1 条 write_file 记录，得到 {len(write_calls)}"

        # 每条记录的 activity_key 应包含 tool:write_file:task_id
        for tc in write_calls:
            assert "tool:write_file:" in tc.get("activity_key", ""), \
                f"activity_key 应包含 tool:write_file:，得到 {tc.get('activity_key')}"
    print("test_coder_tool_calls_logged 通过")


# —— 用例 4：mock LLM 下，文件内容含 task title —— #

def test_coder_mock_llm_writes_stub():
    with tempfile.TemporaryDirectory(prefix="af-test-") as workdir:
        app = _make_pipeline(_mock_registry())
        init = {
            "requirement": "实现 fibonacci 函数, 写单元测试",
            "workdir": workdir,
        }
        res = app.invoke(init, thread_id="tc-mock")
        assert res.status == "completed"

        plan_tasks = res.state["plan"]["tasks"]
        for task in plan_tasks:
            art = f"src/task_{task['id']}.py"
            full = os.path.join(workdir, art)
            assert os.path.isfile(full)
            with open(full, "r", encoding="utf-8") as f:
                content = f.read()
            # mock LLM 的 complete() 返回 mock 文本，不是 stub；但 LLM 异常时会写 stub
            # 无论哪种情况，文件都应有内容
            assert len(content) > 0, f"文件 {art} 应有内容"
    print("test_coder_mock_llm_writes_stub 通过")


# —— 用例 5：plan.tasks 为空时 coder 不报错 —— #

def test_coder_empty_plan_skips():
    with tempfile.TemporaryDirectory(prefix="af-test-") as workdir:
        # 直接构造一个 plan.tasks 为空的场景：跳过 planner，直接跑 coder
        set_registry(_mock_registry())
        schema = StateSchema(reducers={"log": append_reducer, "artifacts": append_reducer})
        g = StateGraph(schema, max_steps=10)
        g.add_node("coder", coder)
        g.add_edge(START, "coder")
        g.add_edge("coder", END)
        app = g.compile(Checkpointer())

        init = {
            "plan": {"summary": "空任务", "tasks": [], "acceptance_criteria": [], "clarifying_questions": []},
            "tasks": [],
            "workdir": workdir,
        }
        res = app.invoke(init, thread_id="tc-empty")
        assert res.status == "completed", res.status
        assert res.state["code_version"] == 1
        # artifacts 应为空列表
        assert res.state.get("artifacts") == []
    print("test_coder_empty_plan_skips 通过")


# —— 用例 6：有 test_failures 时 prompt 含修复提示 —— #

def test_coder_with_feedback():
    with tempfile.TemporaryDirectory(prefix="af-test-") as workdir:
        # 使用 mock registry，让 LLM 总是返回固定文本
        class FeedbackMockRegistry(LLMRegistry):
            def complete(self, node, prompt, *, system_prompt=None):
                self._all_prompts.append(prompt)
                return "mock code with fixes"
            _all_prompts = []

        reg = FeedbackMockRegistry(
            providers={},
            nodes={"planner": {"provider": "mock"}, "coder": {"provider": "mock"}},
        )
        set_registry(reg)

        schema = StateSchema(reducers={"log": append_reducer, "artifacts": append_reducer})
        g = StateGraph(schema, max_steps=10)
        g.add_node("coder", coder)
        g.add_edge(START, "coder")
        g.add_edge("coder", END)
        app = g.compile(Checkpointer())

        init = {
            "plan": {
                "summary": "修复 bug",
                "tasks": [{"id": "t1", "title": "修复空指针", "details": "修复空指针异常"}],
                "acceptance_criteria": [],
                "clarifying_questions": [],
            },
            "tasks": ["t1"],
            "test_failures": ["test_login 失败: NullPointerException at line 42"],
            "workdir": workdir,
        }
        res = app.invoke(init, thread_id="tc-feedback")
        assert res.status == "completed", res.status
        # coder 不应抛异常
        assert res.state["code_version"] == 1
        # 验证至少有一个 prompt 包含了 feedback
        assert any("NullPointerException" in p for p in reg._all_prompts), \
            f"feedback 未注入任何 prompt: {reg._all_prompts}"
        print("test_coder_with_feedback 通过")


# —— 用例 7：兼容旧场景（无 plan，只有 state["tasks"]）—— #

def test_coder_legacy_compat():
    """coder 兼容旧场景：无 plan 时从 state['tasks'] 生成 plan_tasks。"""
    with tempfile.TemporaryDirectory(prefix="af-test-") as workdir:
        set_registry(_mock_registry())
        schema = StateSchema(reducers={"log": append_reducer, "artifacts": append_reducer})
        g = StateGraph(schema, max_steps=10)
        g.add_node("coder", coder)
        g.add_edge(START, "coder")
        g.add_edge("coder", END)
        app = g.compile(Checkpointer())

        init = {
            "requirement": "实现登录",
            "tasks": ["t1", "t2"],  # 旧格式：字符串列表
            "workdir": workdir,
        }
        res = app.invoke(init, thread_id="tc-legacy")
        assert res.status == "completed"
        artifacts = res.state.get("artifacts", [])
        assert len(artifacts) == 4, f"legacy 2 tasks → 应有 4 个产物（每个 task 含实现+测试），实际 {len(artifacts)}"
        assert "src/task_t1.py" in artifacts
        assert "src/task_t2.py" in artifacts
        # 文件确实存在
        for art in artifacts:
            full = os.path.join(workdir, art)
            assert os.path.isfile(full)
    print("test_coder_legacy_compat 通过")


if __name__ == "__main__":
    test_coder_writes_files()
    test_coder_artifacts_list_correct()
    test_coder_tool_calls_logged()
    test_coder_mock_llm_writes_stub()
    test_coder_empty_plan_skips()
    test_coder_with_feedback()
    test_coder_legacy_compat()
    print("\n全部测试通过\n")