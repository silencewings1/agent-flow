# CR Backlog 修复计划

> 三份 CR 报告遗留：28 个未修问题（13 P1 + 15 P2 + 5 设计层观察）。
> 本计划按"影响/成本"分三轮，每轮标准流程：Dev Agent → CR Agent → PM 合并。

---

## 总览

| 来源 | P1 未修 | P2 未修 | 设计层 |
|------|---------|---------|--------|
| Wave 1 CR (`docs/review-notes-p1.md`) | 6 | 7 | 5 |
| Wave 2 CR (`docs/review-notes-p1-wave2.md`) | 2 | 5 | 3 |
| dev_py37 CR (`docs/review-notes-py37.md`) | 2 | 7 | 4 |
| **合计** | **10** | **19** | **12** |

---

## Round 1：立刻修（10 个 P1，~2 小时）

影响正确性/可观测性/资源泄漏，成本低。

### Wave 1 CR P1 遗留（6 个）

| # | 问题 | 文件:行 | 修复方案 | 量 |
|---|------|---------|---------|----|
| 1 | `apply_patch("x.py", "")` 静默成功 | `tools.py:116` | 开头加 `if not diff.strip(): raise ValueError(...)` | ~3 行 |
| 2 | planner 中文逗号 split 是死代码 | `nodes.py:46-54` | 调整 fallback 顺序：优先用 seed task（保留确定性拆分），mock fallback 为兜底 | ~10 行 |
| 3 | `parse_plan_from_llm` 多 JSON 块只取第一个 | `plan.py:119-133` | `search` → `finditer`，依次尝试直到 validate 通过 | ~10 行 |
| 4 | `Plan.validate()` 不检查 details 类型 | `plan.py:49-64` | 加 `isinstance(t.get("details"), str)` 检查 | ~3 行 |
| 5 | `human_review` decision docstring 缺失 | `nodes.py:161` | 写清楚 resume 值可以是 `bool` 或 `{"approve": bool}` | docstring |
| 6 | mock fallback 丢弃 LLM partial 信息 | `plan.py:136` | 走 mock 前用关键词提取（如 `？` 结尾的句子 → clarifying_questions） | ~8 行 |

### Wave 2 CR P1 遗留（2 个）

| # | 问题 | 文件:行 | 修复方案 | 量 |
|---|------|---------|---------|----|
| 7 | pytest 不可用行为不当 | `nodes.py:243` | debugger 启动时做 `pytest --version` 探测，不可用则 fallback 到旧 pass_at_version 行为 | ~5 行 |
| 8 | coder 临时目录泄漏 | `nodes.py:125-126` | 当 `workdir_explicit=False` 时，coder 返回前用 try/finally 清理自建临时目录 | ~5 行 |

### dev_py37 CR P1 遗留（2 个）

| # | 问题 | 文件:行 | 修复方案 | 量 |
|---|------|---------|---------|----|
| 9 | PEP 604/PEP 585 正则误判率极高 | `test_py37_compat.py:169-229` | 改用 AST 检测（`ast.Subscript` + `ast.BinOp(op=ast.BitOr)` 在注解上下文） | ~20 行 |
| 10 | `verify_py37.sh` PATH 依赖脆弱 | `scripts/verify_py37.sh:9` | 加 shebang `#!/usr/bin/env bash`，用 `"${PYTHON3:-python3}"` 允许 CI 注入 | ~3 行 |

### Round 1 分支策略

单个分支 `fix/cr-backlog-round1`，10 个修复在 5 个文件中，无冲突。

```
fix/cr-backlog-round1:
  agentflow/tools.py   — #1 (apply_patch 空 diff)
  agentflow/nodes.py   — #2 (planner 死代码) + #5 (docstring) + #7 (pytest 探测) + #8 (临时目录清理)
  agentflow/plan.py    — #3 (多 JSON 块) + #4 (details 类型) + #6 (partial 信息)
  test/test_py37_compat.py — #9 (AST 检测)
  scripts/verify_py37.sh   — #10 (PATH)
```

### Round 1 验收标准

- 现有 9 套测试（94 用例）全过
- 新增的修复有对应测试覆盖（见下）
- demo.py 7 场景全过
- Python 3.7 下也全过

### Round 1 新增测试

| 对应修复 | 测试 | 预期 |
|---------|------|------|
| #1 | `test_apply_patch_rejects_empty_diff` | 已存在（P1 Wave 1 CR 修复时加的） |
| #2 | `test_planner_seed_tasks_used_in_fallback` | fallback 时 seed task 数 = 逗号拆分后的数量 |
| #3 | `test_parse_multiple_json_blocks_tries_all` | 第 1 块非法、第 2 块合法 → 用第 2 块 |
| #4 | `test_plan_validate_rejects_non_string_details` | details 为 100 层嵌套 list → validate 报错 |
| #7 | `test_debugger_pytest_not_available` | 无 pytest 时 fallback 到 pass_at_version |
| #8 | `test_coder_cleanup_implicit_workdir` | workdir 未显式传入 → coder 返回后目录被清理 |
| #9 | AST 检测替代 regex — 现有 test_py37_compat 用例会自动验证 |

---

## Round 2：后续修（P2 精选，~1 小时）

从 19 个 P2 中选 7 个有价值的。

| # | 来源 | 问题 | 修复量 |
|---|------|------|--------|
| 11 | Wave 1 P2 3.1 | `test_planner.py` 死代码（重复定义 reg） | 删 6 行 |
| 12 | Wave 1 P2 3.2 | `pass_at_version=None` → TypeError | 1 行 `or 3` |
| 13 | Wave 1 P2 3.6 | `apply_patch` 没限定大小 | ~3 行 |
| 14 | Wave 2 P2 3.1 | `plan.tasks = None` 依赖 falsy 巧合 | 1 行显式 `or []` |
| 15 | Wave 2 P2 3.2 | task 缺 id → 文件覆盖 | ~3 行加 warn + 自动分配 id |
| 16 | Wave 2 P2 3.4 | `test_coder_with_feedback` 不验证 feedback 注入 | 测试加 1 行 assert |
| 17 | Wave 2 P2 3.5 | 场景 7 demo 是 dummy coder | 让 dummy coder 真改文件 |

### Round 2 分支策略

单个分支 `fix/cr-backlog-round2`。

---

## Round 3：长期（设计层 + 剩余 P2，需要独立 PR）

这些需要更重的架构改动或跨模块协调，不适合混在一个 fix branch 里。

| # | 来源 | 问题 | 需要 |
|---|------|------|------|
| 18 | Wave 1 设计 4.1 | `run_cmd` 白名单是 theater security | 独立的"沙箱 v2"PR（Docker/gVisor） |
| 19 | Wave 2 设计 4.1 | debugger 绕过 ToolRuntime 架构张力 | 给 ToolRuntime 加 `cwd_override` 参数 |
| 20 | Wave 1 设计 4.5 | `ctx.tool()` key 与 cache invariant 矛盾 | 重设计 activity cache key 含 fn hash |
| 21 | Wave 1 设计 4.2 | P1-2 文档与实现不符（task 拆分） | 等 P2 任务并行时一起解决 |
| 22 | Wave 1 设计 4.4 | `Plan.validate()` 与 `parse_plan_from_llm` 契约 | 需要讨论 API 语义 |

剩余 P2（dev_py37 CR 的 3.1-3.7、Wave 1 CR 的 3.3-3.5, 3.7-3.8、Wave 2 CR 的 3.3）均为**极低影响**（docstring、unicode 边界、计数估算），列入长期 backlog，不在本轮修复。

---

## 时间线

```
Day 1 ─── Round 1: Dev Agent → CR Agent → PM merge (10 P1)
Day 2 ─── Round 2: Dev Agent → CR Agent → PM merge (7 P2)
长期 ─── Round 3: 独立 PR × 5（需要设计讨论）
```

---

## 协作流程（与 P1 一致）

每个 Round：
1. PM 写 fix plan（本文档）
2. Dev Agent 在独立 worktree 实现 + 测试
3. CR Agent 独立审查（读 plan → 跑测试 → 对抗性 fuzz → 写 review-notes）
4. PM 修 CR 反馈 → merge → 更新 Memory

---

## 验收标准（Round 1 + 2 完成后）

- 9 套测试 + 新增修复测试全过
- Python 3.7.17 + 3.14 双环境验证
- demo.py 7 场景全过
- CR backlog 从 28 个降至 13 个（P2 低优先级 + 设计层）
