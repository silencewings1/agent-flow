# P1 Wave 2 CR 审查计划（2026-06-17）

> 范围：P1-3 (`95cbe7f`) 真实 Coder + P1-4 (`8139dc7`) 真实 Debugger，已合并到 master。
> 方法：独立 fresh-eyes 审查，不依赖 Dev 报告，自己看代码、跑测试、做对抗性 fuzz。

## 1. 测试基线

```bash
for t in test_invariants test_activity test_graph test_planner test_review test_tools test_coder test_debugger; do
  PYTHONPATH=. python3 test/$t.py 2>&1 | tail -3
done
python3 demo.py  # 7 场景全过？
```

**记录**：每个套件的通过数，标记任何宽松断言。

## 2. P1-3 真实 Coder 审查点

### 2.1 核心逻辑正确性
- [ ] `coder` 遍历 `plan.tasks`，每个 task 写一个文件 — 确认 plan 为空/None/非法类型时的行为
- [ ] lambda 闭包模式 `fn=lambda p=file_path, c=code: ...` — 默认参数捕获是否正确？（常见 Python 闭包陷阱）
- [ ] `ctx.tool("write_file", key=task_id, ...)` — key 是否真的唯一（不同 task 不撞缓存）
- [ ] 旧场景兼容（无 plan.tasks → 从 state["tasks"] fallback）— 确认 fallback 不破坏 scenario 1-5

### 2.2 边界与错误处理
- [ ] `plan.tasks` 为空列表 → coder 不报错，返回 artifacts=[]
- [ ] `plan.tasks` 含非法 task（缺 id、缺 title、id 含 `/` `\` 等）→ 不 crash
- [ ] LLM 失败 → 写 stub 文件（"mock code"），pipeline 继续
- [ ] 同 task 多次写入（回环：coder → debugger fail → coder 再写）— 第二次 `ctx.tool("write_file", key=task_id, ...)` 是否撞缓存导致**不实际写**？
- [ ] **关键对抗**：coder 在回环中第二次被调用（debugger fail → 退回 coder），step 变了但 key 还是 `task_id` — activity 缓存键是 `(thread_id, node, step, key)`，step 不同所以**不会撞缓存**？验证！

### 2.3 安全与资源
- [ ] `tempfile.mkdtemp` 创建的 workdir — 生命周期是否可控？demo 场景 6 手动清理了，但生产路径下谁清理？
- [ ] 文件写入路径 — `os.path.join(workdir, "src", f"task_{task_id}.py")`，task_id 来自 plan，是否可能路径穿越？
- [ ] 大量 task（100+）时是否合理？

### 2.4 测试覆盖
- [ ] `test_coder.py` 的 7 个用例是否真正覆盖了 spec 列出的 5 个必查项？
- [ ] 是否存在"假绿色"测试（try/except 吞断言失败、assert True 等）

## 3. P1-4 真实 Debugger 审查点

### 3.1 核心逻辑正确性
- [ ] 测试发现：`**/test_*.py` + `**/*_test.py` — 用 `os.walk`，确认正确
- [ ] pytest 调用：`subprocess.run(cwd=workdir)` — 为什么不用 ToolRuntime.run_cmd？
- [ ] **关键**：CR 修复后 `python3` 已从白名单移除，debugger 用的是 `pytest` 直接命令还是 `python3 -m pytest`？
- [ ] FAILED 行正则：`FAILED\s+(\S+::\S+)\s*[-:]\s*(.*)` — 对不同 pytest 版本/输出格式是否兼容？
- [ ] fallback 路径（无 workdir → pass_at_version）— 确认 scenario 1-5 不受影响

### 3.2 边界与错误处理
- [ ] workdir 存在但无测试文件 → `tests_passed=True`（默认通过，log 提示）
- [ ] workdir 有测试但 pytest 不可用（PATH 无 pytest）→ 怎么处理？
- [ ] pytest 超时 → 怎么处理？
- [ ] pytest 输出不含 FAILED 行（如只输出 "error" 或 "ERRORS"）→ 怎么处理？
- [ ] 测试文件有语法错误（pytest 收集阶段失败）→ 怎么处理？
- [ ] `test_failures` 为空但 `tests_passed=False`（pytest 非零退出但不是 FAILED 导致的）→ 是否可能出现？

### 3.3 安全
- [ ] 直接 `subprocess.run` 而不走 ToolRuntime — 路径校验、白名单等安全机制全部绕过？
- [ ] pytest 命令拼接 `test_files_arg = " ".join(test_files)` — 文件名含空格/特殊字符会怎样？
- [ ] 超时默认 120s — 对于大型测试套件够吗？无限挂起的风险？

### 3.4 测试覆盖
- [ ] `test_debugger.py` 的 6 个用例是否真正覆盖 spec 列出的 6 个必查项？
- [ ] 是否真的端到端验证了"失败 → coder 修 → 通过"回环？

## 4. 跨节点集成

- [ ] coder 产出 `artifacts` + debugger 消费 `artifacts` — 数据流是否正确？
- [ ] coder 产出 `workdir` + debugger 消费 `workdir` — 生命周期一致？
- [ ] 回环：coder → debugger(fail) → coder → debugger(pass) — 完整链路是否工作？
- [ ] scenario 7 是真的端到端（coder 写文件 → debugger 跑 pytest → 失败 → 退回 coder → coder 重写 → debugger 再跑 → 通过）还是简化版？

## 5. 对抗性 fuzz

- [ ] plan.tasks = `[{"id": "../../etc", "title": "x"}]` — 路径穿越？
- [ ] workdir 不是目录（是个文件路径）→ coder/debugger 行为？
- [ ] pytest 输出巨大（1MB stdout）→ 正则/内存 OK？
- [ ] 多个测试文件，部分通过部分失败 → `test_failures` 只含失败的？
- [ ] 同 step 内 coder 写文件 + debugger 读文件 → 文件系统一致性？

## 6. 代码质量

- [ ] 命名一致性（plan_tasks / legacy_tasks / artifacts / workdir）
- [ ] docstring 完整性（coder 和 debugger 的新 docstring 是否准确描述行为）
- [ ] 死代码（fallback 路径是否可达？如 coder 的 `legacy_tasks` 分支）
- [ ] 导入整洁性（coder/debugger 都加了 `import os, tempfile`，有无重复）

## 审查输出

写到 `/Users/ospacer/cpp_test/agent-flow/docs/review-notes-p1-wave2.md`，格式与 Wave 1 相同：
- P0（必须修）/ P1（建议修）/ P2（细节）/ 设计观察 / 亮点 / 总评
