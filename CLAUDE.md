# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 默认使用 Python 3.14 环境
source ~/.py_ai/bin/activate

python -m demo                                      # Run all 8 demo scenarios
PYTHONPATH=. python test/test_invariants.py          # Run invariant tests
PYTHONPATH=. python -m pytest test/ -v              # Run all test suites
```

No dependencies beyond Python 3.7+ standard library.

## Architecture

A zero-dependency DAG orchestration kernel that combines AgentMesh-style role nodes (Planner/Coder/Debugger/Reviewer) with LangGraph-style super-step execution + checkpointer.

### Core modules

- **`agentflow/graph.py`** — `StateGraph` (define nodes, edges, conditional edges) + `CompiledGraph` (super-step executor). Each super-step runs the current frontier in parallel via `ThreadPoolExecutor`, merges partial updates via reducers, then computes the next frontier. `START`/`END` are reserved sentinel nodes.
- **`agentflow/state.py`** — `StateSchema` with per-key reducers. Default is `overwrite_reducer`; `append_reducer` accumulates lists (used for logs and fan-out merges). `merge()` always returns a new dict (no in-place mutation).
- **`agentflow/checkpoint.py`** — Event-sourcing SQLite checkpointer. Each super-step writes a `Checkpoint` (state snapshot + frontier). The frontier only contains nodes yet to run, so **resume never replays completed nodes** — this is the hard invariant tested in `test_invariants.py`. Also maintains an append-only event log for audit/time-travel.
- **`agentflow/interrupt.py`** — HITL primitives: `Interrupt` exception (pauses the graph), `Command(resume=...)` (injects human response on resume). `interrupt(payload, resume_value)` returns `resume_value` on replay or raises `Interrupt` on first execution.
- **`agentflow/llm.py`** — `LLMRegistry` loads per-node LLM config from JSON. Each node resolves `provider → protocol → (anthropic|openai/chat|openai/response|mock)`. Uses openai / anthropic official SDKs. API keys read from env vars only, never from config file. Unconfigured nodes fall back to mock.
- **`agentflow/nodes.py`** — Four AgentMesh nodes (planner, coder, debugger, reviewer) + conditional routing functions. **Control flow is deterministic** (task splitting, version gating, pass/fail decisions) — LLM only produces content text, so the pipeline is reproducible without real API keys.

### Key design decisions

- **No rerun on resume**: The frontier stores only unexecuted nodes. On resume, state is restored from checkpoint and execution continues from the frontier — completed nodes are never replayed.
- **Deterministic merge order**: Within a super-step, partial updates are merged in batch order (not by completion time), ensuring reproducible state.
- **Thread pool for I/O-bound parallelism**: LLM calls benefit from threading. For CPU-bound workloads, swap to process pool.
- **max_steps guard**: Cyclic graphs (e.g., debugger → coder loop) are bounded by `max_steps` (default 50); exceeding it yields `status=failed`.

### Adding a new LLM provider

Add an entry to `llm_config.json` under `providers` with `models: [...]` (list of available models) and `protocol: "openai/chat"` (Chat Completions), `protocol: "openai/response"` (Responses API), or `protocol: "anthropic"`. No code changes needed. The `protocol` field maps to the dispatch table in `llm.py:_DISPATCH`. Model inheritance: `nodes[name].model → defaults.default_model → provider.default_model → provider.models[0]`.

## Multi-session collaboration (多窗口协作)

本项目的开发流程通过 **3 个 Claude Code 终端窗口**协作完成，各窗口通过文件系统和 Memory 共享上下文：

| 窗口 | 角色 | 职责 | 产出 |
|------|------|------|------|
| 窗口 1 | **项目经理** | 需求分析、任务拆分、方案设计、进度跟踪 | `plan/plan.md`、Memory |
| 窗口 2 | **开发** | 按需求文档写代码、修 bug、跑 demo | 代码变更 + git commit |
| 窗口 3 | **代码审查及测试** | review diff、运行测试、记录问题 | `plan/review-notes.md` |

### 协作流程（关键：CR 不可跳过）

```
窗口 1(PM)：需求分析 → 产出 plan/plan.md + 写入 Memory
窗口 2(Dev)：读取 plan/plan.md → 写代码实现 → git commit
窗口 3(CR)：  git diff 看改动 → 跑测试 → 产出 plan/review-notes.md
窗口 2(Dev)：读取 plan/review-notes.md → 修 bug → 再次 commit
窗口 1(PM)：CR 确认通过 → git merge → 删除 feature 分支
```

**⚠️ PM 合并必须在 CR 通过之后，绝对不可跳过 CR 步骤。**

此规则来自三次教训：Wave 1（PM 跳过 CR 直接合并，事后 CR 发现 4 个 P0）、Wave 2（同样跳过，事后发现 3 个 P0）、Round 1（跳过，事后发现 2 个 P1）。每次事后补救都能发现问题，证明独立 CR 不是形式主义。

PM 在子 agent 中担任的角色：打开多个 agent，分别承担 Dev、CR 等角色，PM 负责规划、验收和合并。流程必须严格 Dev → CR → PM 串行。

### 窗口间通信方式

会话之间不能直接对话，通过以下机制同步：

- **`plan/` 目录** — 项目计划（`plan-*.md`）、审查产出（`review-notes*.md`、`review-checklist*.md`），开发窗口实现前先读取；审查反馈也写入此目录
- **`docs/` 目录** — 知识库（研究报告、分析报告等长期参考文档），不用于日常协作流转
- **Memory 系统** — 项目经理把关键决策、里程碑、约束写入 memory，所有窗口自动加载
- **git log / diff** — 开发窗口 commit 后，审查窗口通过 `git diff` 获取改动
- **Git worktree** — 如需并行开发多个分支互不干扰，使用 `EnterWorktree` 创建隔离工作区
