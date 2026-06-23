# CR Backlog 修复计划

> 三份 CR 报告遗留：28 个未修问题（13 P1 + 15 P2 + 5 设计层观察）。
> 本计划按"影响/成本"分三轮，每轮标准流程：Dev Agent → CR Agent → PM 合并。

---

## 总览

| 来源 | P1 未修 | P2 未修 | 设计层 |
|------|---------|---------|--------|
| Wave 1 CR (`docs/review-notes-p1.md`) | 0 | 7 | 5 |
| Wave 2 CR (`docs/review-notes-p1-wave2.md`) | 0 | 5 | 3 |
| dev_py37 CR (`docs/review-notes-py37.md`) | 0 | 7 | 4 |
| **合计** | **0** | **19** | **12** |

> **2026-06-23 更新**：全部 10 个 P1 已在 commit `86f7ce7`（fix: CR Backlog Round 1）中修复。
> 当前无未修的 P1 项。

---

## Round 1：已完成（10 个 P1，commit `86f7ce7`）

影响正确性/可观测性/资源泄漏，成本低。

### Wave 1 CR P1 遗留（6 个）— 已完成

| # | 问题 | 状态 | 修复commit |
|---|------|------|-----------|
| 1 | `apply_patch("x.py", "")` 静默成功 | ✅ | 86f7ce7 |
| 2 | planner 中文逗号 split 是死代码 | ✅ | 86f7ce7 |
| 3 | `parse_plan_from_llm` 多 JSON 块只取第一个 | ✅ | 86f7ce7 |
| 4 | `Plan.validate()` 不检查 details 类型 | ✅ | 86f7ce7 |
| 5 | `human_review` decision docstring 缺失 | ✅ | 86f7ce7 |
| 6 | mock fallback 丢弃 LLM partial 信息 | ✅ | 86f7ce7 |

### Wave 2 CR P1 遗留（2 个）— 已完成

| # | 问题 | 状态 | 修复commit |
|---|------|------|-----------|
| 7 | pytest 不可用行为不当 | ✅ | 86f7ce7 |
| 8 | coder 临时目录泄漏 | ✅ | 86f7ce7 |

### dev_py37 CR P1 遗留（2 个）— 已完成

| # | 问题 | 状态 | 修复commit |
|---|------|------|-----------|
| 9 | PEP 604/PEP 585 正则误判率极高 | ✅ | 86f7ce7 |
| 10 | `verify_py37.sh` PATH 依赖脆弱 | ✅ | 86f7ce7 |

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
Day 1 ─── Round 1: ✅ 已完成 (commit 86f7ce7)
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
