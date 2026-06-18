# P1 CR 审查清单（Wave 1）

> CR 窗口收到 Dev 提交的 feature 分支后，按本清单逐项核查。所有"必查"项必须通过，"建议"项可标注意见但不阻塞。

## 通用检查（每个分支都过一遍）

### 必查

- [ ] **分支正确**：`feat/p1-{X}-*` 命名，基准 master
- [ ] **diff 范围合理**：只动本任务相关文件，没有夹带无关修改
- [ ] **未提交 docs/plan-p1.md / docs/review-checklist-p1.md**（这些是 PM/CR 产出）
- [ ] **commit message 含 P1-X 标识**：`P1-X: <summary>`
- [ ] **`PYTHONPATH=. python3 -m unittest discover test/` 全过**
- [ ] **`python3 demo.py` 现有 5 场景不破坏**
- [ ] **新文件有 docstring**，导出符号在 `__init__.py` 注册
- [ ] **无新增三方依赖**（保持零依赖原则，标准库实现）

### 建议

- [ ] 函数职责单一，节点/工具不超过 ~80 行
- [ ] 错误信息含具体上下文（路径、行号、节点名）
- [ ] mock 兜底路径完整，CI 不需要 API key

---

## P1-1: ToolRuntime

### 必查功能

- [ ] `agentflow/tools.py` 存在，`ToolRuntime` 类导出
- [ ] 5 类工具方法全部实现：`read_file` / `write_file` / `list_dir` / `apply_patch` / `run_cmd` / `git_diff`
- [ ] `NodeContext.tool(name, fn, **kwargs)` 方法添加（薄包装 activity）
- [ ] 沙箱：`workdir = {root}/af-{thread_id}/`，`cleanup()` 释放
- [ ] `run_cmd` 拒绝路径含 `..`（抛 PermissionError）
- [ ] `run_cmd` 有超时机制（默认 60s），超时后子进程被 kill

### 必查测试（13 用例全过）

- [ ] `test_read_file_existing` / `test_read_file_missing`
- [ ] `test_write_file_new_path` / `test_write_file_overwrite`
- [ ] `test_apply_patch_valid` / `test_apply_patch_invalid`
- [ ] `test_run_cmd_success` / `test_run_cmd_nonzero_exit` / `test_run_cmd_rejects_dotdot` / `test_run_cmd_timeout`
- [ ] `test_tool_caching`（同 thread 同 node 同 step 调 read_file 两次，fs 访问 1 次）
- [ ] `test_cleanup_removes_workdir`
- [ ] `test_git_diff_in_non_git_repo`（返回空串）

### 架构一致性

- [ ] 工具方法内部走 `ctx.activity(f"tool:{name}", fn, input_summary=...)`，**不**直接调 checkpointer
- [ ] 工具失败时抛异常，让 ctx.activity 自动记录 status=exception
- [ ] `ToolRuntime` 不持有 checkpointer 引用（依赖 ctx 注入）

---

## P1-2: 结构化 Planner

### 必查功能

- [ ] `agentflow/plan.py` 存在，`Plan` dataclass 导出
- [ ] `Plan` 含 4 字段：`summary` / `tasks` / `acceptance_criteria` / `clarifying_questions`
- [ ] `Plan.validate()` 返回错误列表（空=合法）
- [ ] `parse_plan_from_llm(llm_text, requirement)` 实现三层 fallback：
  1. `json.loads(llm_text)`
  2. 正则提取 ```json ... ``` 代码块
  3. 确定性 mock（单 task = requirement 本身）
- [ ] `nodes.planner` 重写，返回 `{"plan": dict, "tasks": [task_ids], "log": [...]}`
- [ ] `state["tasks"]` 仍存在（取自 plan.tasks 的 id 列表，兼容下游）

### 必查测试（7 用例全过）

- [ ] `test_plan_valid_empty_acceptance`
- [ ] `test_plan_invalid_empty_tasks`（validate 返回错误）
- [ ] `test_parse_pure_json`
- [ ] `test_parse_json_in_code_block`（正则提取）
- [ ] `test_parse_garbage_falls_back_to_mock`
- [ ] `test_planner_node_returns_structured_state`
- [ ] `test_planner_works_with_mock_llm`（demo 不需要 key 也能跑）

### 兼容性

- [ ] 旧 `state["plan"]` 现在是 dict（之前是 str），demo 同步更新无报错
- [ ] 现有 scenario_pipeline 仍能完整跑通

---

## P1-5: Review 分层

### 必查功能

- [ ] `nodes.py` 中 `reviewer` 拆为 `ai_review` + `human_review` 两个独立函数
- [ ] `ai_review`：调 LLM，不调 `ctx.interrupt()`，返回 `{"ai_review": comments, "log": [...]}`
- [ ] `human_review`：调 `ctx.interrupt(payload)`，payload 含 `ai_review` 字段
- [ ] 新增路由函数 `route_after_human_review`（替代 `route_after_review`）
- [ ] 旧 `state["review_note"]` 改名为 `state["human_review_decision"]`
- [ ] `demo.py` 中 4 处 `reviewer` / `route_after_review` 引用同步更新：
  - `add_node("reviewer", reviewer)` → `add_node("ai_review", ai_review)` + `add_node("human_review", human_review)`
  - `add_conditional_edges("reviewer", route_after_review)` → `add_conditional_edges("human_review", route_after_human_review)`
  - `scenario_timetravel` 同上

### 必查测试（6 用例全过）

- [ ] `test_ai_review_does_not_interrupt`（跑到 ai_review 后 status=interrupted=False）
- [ ] `test_human_review_interrupts_with_ai_review_in_payload`
- [ ] `test_human_review_resume_reject_returns_to_coder`
- [ ] `test_human_review_resume_approve_returns_to_end`
- [ ] `test_state_ai_review_is_string`
- [ ] `test_state_approved_is_bool`

### 兼容性

- [ ] 旧测试 `test_invariants.py` 不依赖 reviewer 名字，仍通过
- [ ] `state["approved"]` 行为不变（True/False 含义一致）

---

## CR 输出规范

CR 审查完成后，写入 `docs/review-notes-p1.md`，结构如下：

```markdown
# P1 Wave 1 CR 审查记录

## feat/p1-1-tool-runtime（commit xxx）

### 测试结果
- 单元测试：X/Y 通过
- demo 场景：5/5 通过
- test_invariants：2/2 通过

### 问题清单
1. [严重] <描述> — 必须修复
2. [一般] <描述> — 建议修复
3. [细节] <描述> — 可选

### 亮点
- ...

### 结论
- [ ] 通过，可合并
- [ ] 不通过，需 Dev 修 bug 后再审
```

每个分支一节，结论明确"通过/不通过"。

---

## PM 合并标准

CR 标记"通过"后，PM 才执行 merge：
1. `git checkout master`
2. `git merge --no-ff feat/p1-X-*`（保留分支信息）
3. 跑完整测试套件
4. `git branch -d feat/p1-X-*` 删除已合并分支
5. 更新 `MEMORY.md` 标记 P1-X 已合并
