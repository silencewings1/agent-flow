# P1 Wave 2 CR 审查记录（2026-06-17）

> 范围：P1-3 真实 Coder (`95cbe7f`) + P1-4 真实 Debugger (`8139dc7`)，已合并到 master。
> 方法：独立 fresh-eyes 审查，8 个测试套件 + demo.py + 14 项对抗性 fuzz。

## 0. 测试通过情况

| 套件 | 结果 | 备注 |
|------|------|------|
| test_invariants | PASS | 全部通过 |
| test_activity | PASS | 全部通过 |
| test_graph | PASS | 17/17 |
| test_planner | PASS | 全部通过 |
| test_review | PASS | 6/6 |
| test_tools | PASS | 20/20 |
| test_coder | PASS | 7/7，断言均严格（无 assert True / try-except 吞异常） |
| test_debugger | PASS | 6/6，断言均严格 |
| demo.py | PASS | 7 场景全部完成 |

**测试质量评价**：断言严谨，无宽松断言或假绿色。但覆盖盲区见下文。

## 1. 严重问题（P0）

### 1.1 debugger FAILED 正则无法解析 `TestClass::test_method` 格式

- **文件**: `agentflow/nodes.py:285`
- **现象**: 正则 `r"FAILED\s+(\S+?::\S+?)\s*[-:]\s*(.*)"` 中的 `\S+?::\S+?` 使用非贪婪匹配，在遇到 `test_file.py::TestClass::test_method` 时只匹配到 `test_file.py::TestClass`，剩下的 `::test_method - AssertionError` 被 `\s*[-:]` 消费掉。结果是 `test_name` 被截断为 `"test_file.py::TestClass"`，`error_msg` 变成 `":test_method - AssertionError"`。

- **复现**:
  ```python
  import re
  line = "FAILED test_file.py::TestClass::test_method - AssertionError"
  m = re.match(r"FAILED\s+(\S+?::\S+?)\s*[-:]\s*(.*)", line)
  # m.group(1) → "test_file.py::TestClass"  # 错误！
  # m.group(2) → ":test_method - AssertionError"  # 错误！
  ```

- **影响范围**: 任何使用 pytest class-based 测试的场景（`class TestXxx: def test_yyy`）都会触发此 bug，导致 `test_failures` 中的 `test_name` 错误。这会破坏回环中 coder 获取的 feedback 质量。

- **建议**: 改用 `(\S+(?:::[\w\[\], ]*\S+))` 或更简单的 `(\S+?(?:::\S+)+)` 来匹配含任意数量 `::` 的 test name。参考修复：
  ```python
  m = re.match(r"FAILED\s+(\S+(?:::[\w\[\], ]*\S+))\s*[-:]\s*(.*)", line)
  ```

- **严重程度理由**: 正则 bug 导致 test_failures 数据损坏，直接影响 coder→debugger 回环的修复质量。pytest class-based 测试是常见写法，不是边缘情况。

### 1.2 debugger 的 subprocess 绕过了全部安全机制

- **文件**: `agentflow/nodes.py:243-245`
- **现象**: debugger 内部 `_run_pytest()` 直接调用 `_subprocess.run(cmd, shell=True, ...)` 而不是走 `ToolRuntime.run_cmd()`。代码注释解释了原因（"ToolRuntime 有自己的沙箱 workdir"），但这意味着：
  - **白名单绕过**：不受 `_CMD_ALLOWED_PREFIXES` 约束
  - **路径校验绕过**：不受 `_check_no_dotdot` 约束
  - **workdir 校验绕过**：不受 `_check_paths_in_workdir` 约束
  - `ctx.tool("run_cmd", ...)` 的包装只提供了 activity 缓存和审计日志，但**没有**执行 ToolRuntime 的安全检查

- **复现**: 阅读 `agentflow/nodes.py:240-258`，`_run_pytest()` 闭包内的 `_subprocess.run(cmd, shell=True, cwd=workdir, ...)` 是裸 subprocess 调用，ToolRuntime 的 `run_cmd` 方法完全未被调用。

- **实际风险**:
  1. 命令注入：`test_files_arg = " ".join(test_files)` 没有转义（见 P0 1.3）
  2. 即使将来加强白名单，debugger 也会绕过
  3. 如果将来有人修改代码在 workdir 内放入恶意文件名，可能被注入

- **建议**: 两种方案选一：
  A. 在 `_run_pytest()` 内部手动调用 `_check_no_dotdot(cmd)` 和 `_check_paths_in_workdir`
  B. 给 `ToolRuntime.run_cmd()` 增加一个 `cwd_override` 参数，让 debugger 可以指定 workdir 但仍走安全检查

- **严重程度理由**: 这是 P1 Wave 2 新增代码中唯一绕过安全框架的路径。虽然当前 pytest 命令是硬编码的，但文件名拼接（1.3）使得注入成为可能。

### 1.3 debugger test_files 拼接存在 shell 注入风险

- **文件**: `agentflow/nodes.py:237-238`
- **现象**: `test_files_arg = " ".join(test_files)` 后接 `cmd = f"pytest {test_files_arg} --tb=short -q"`，然后传给 `shell=True` 的 `subprocess.run`。如果文件名含空格，会被 shell 解析为多个参数；如果文件名含 `` ` ``、`$()`、`;` 等 shell 元字符，会导致命令注入。

- **复现**:
  ```python
  test_files = ["test foo.py", "test bar.py"]
  cmd = f"pytest {' '.join(test_files)} --tb=short -q"
  # → "pytest test foo.py test bar.py --tb=short -q"
  # shell 解析为: pytest, test, foo.py, test, bar.py, --tb=short, -q
  ```

- **实际风险**: 当前 test_files 来自 `os.walk(workdir)` 的文件名，而 workdir 由 coder 创建，文件名由 `task_id` 决定。如果 `task_id` 包含 shell 元字符（来自 plan），攻击链为：plan.task.id → coder 创建文件名 → debugger os.walk 发现 → 拼入 shell 命令。

- **建议**: 使用 `shlex.join(test_files)` 代替 `" ".join(test_files)`。`shlex` 是 Python 标准库，已在 `tools.py` 中使用。

- **严重程度理由**: 虽然当前攻击面受限于 plan.task.id 的输入源，但 shell 注入是安全红线，且修复只需一行代码。

## 2. 一般问题（P1）

### 2.1 pytest 收集失败时静默误报

- **文件**: `agentflow/nodes.py:278-292`
- **现象**: 当测试文件有语法错误时，pytest 在收集阶段就失败（exit_code=2），stdout 包含 `ERRORS` 而不是 `FAILED`。此时：
  - `tests_passed = False`（因为 exit_code != 0）
  - `failures = []`（因为正则没匹配到 FAILED 行）
  - 返回的 state 显示"测试失败"但没有任何 test_failure 信息
  - 回环中 coder 看到空的 test_failures，不知道该怎么修

- **复现**: 对抗测试 5 已验证。创建一个语法错误的测试文件，pytest 输出不含 `FAILED` 行，`test_failures` 为空列表。

- **建议**: 增加对 exit_code == 2 的检测，或当 `exit_code != 0 and not failures` 时生成一个 fallback failure 条目，包含 stderr 摘要。

- **严重程度理由**: 会导致回环"空转"——debugger 报失败但 coder 看不到具体失败信息，浪费 max_steps。

### 2.2 pytest 不可用时行为不当

- **文件**: `agentflow/nodes.py:243`
- **现象**: 如果系统 PATH 中没有 `pytest`，`subprocess.run(..., shell=True)` 返回 exit_code=127，stderr 为 `"/bin/sh: pytest: command not found"`。此时：
  - `tests_passed = False`
  - `failures = []`（正则没匹配到 FAILED）
  - 回环中 coder 没有修复线索

- **建议**: 在 debugger 启动时做一次 pytest 可用性探测（`subprocess.run(["pytest", "--version"], ...)`），不可用时直接 fallback 到旧行为或报清晰错误。

### 2.3 coder workdir 临时目录泄漏

- **文件**: `agentflow/nodes.py:125-126`
- **现象**: 当 `"workdir" not in state`（即旧场景 1-5），coder 内部调用 `tempfile.mkdtemp(prefix="af-coder-")` 创建临时目录，文件被写入但 `workdir_explicit=False` 导致 workdir 路径不写回 state，调用者无法获取路径来清理。每次运行场景 1 demo 泄漏 1 个空目录。

- **复现**: 对抗测试 14 已验证。运行 `demo.py` 场景 1 后检查 `/tmp/af-coder-*`。

- **建议**: 当 `workdir_explicit=False` 时，在 coder 返回前清理自己创建的临时目录（用 `try/finally` 或 `shutil.rmtree`）。

## 3. 细节问题（P2）

### 3.1 `plan.tasks = None` 依赖 falsy 巧合

- **文件**: `agentflow/nodes.py:111`
- **现象**: `plan_dict.get("tasks", [])` 当 key 存在但值为 `None` 时返回 `None`。`not None` 为 `True`，所以会进入 legacy_tasks fallback 分支。行为正确但意图不明确——代码没有显式处理 `None` 情况。

- **建议**: 加一行 `if plan_tasks is None: plan_tasks = []` 或改为 `plan_dict.get("tasks") or []`。

### 3.2 `plan.tasks` 含非法 task（缺 id）时行为

- **文件**: `agentflow/nodes.py:130`
- **现象**: `task.get("id", "unknown")` — 如果 task 缺 id，所有 task 都会写到 `task_unknown.py`，导致后面 task 的文件覆盖前面的。

- **建议**: 如果 task 缺 id，至少打一个 warning，或用 enumerate 生成唯一 id。

### 3.3 FAILED 正则不支持前导空格

- **文件**: `agentflow/nodes.py:285`
- **现象**: `re.match(r"FAILED\s+...")` 要求 FAILED 在行首。但 pytest 的 short test summary info 部分有时有前导空格。对抗测试 3 中 `"  FAILED mod.py::test - error"` 未被匹配。

- **建议**: 改为 `re.match(r"\s*FAILED\s+...")` 或 `re.search`。

### 3.4 `test_coder_with_feedback` 测试的 mock 不验证 feedback 注入

- **文件**: `test/test_coder.py:189-228`
- **现象**: `FeedbackMockRegistry.complete()` 把 prompt 存到 `self._last_prompt` 但 `test_coder_with_feedback` 测试从未 assert prompt 中是否包含 feedback 内容。测试只验证了 `status == "completed"` 和 `code_version == 1`，未验证 coder 是否真的看到了 test_failures。

- **建议**: 在测试末尾增加 `assert "NullPointerException" in reg._last_prompt`。

### 3.5 场景 7 demo 的回环是 dummy coder 不是真实 coder

- **文件**: `demo.py:277-281`
- **现象**: 场景 7 的 `dummy_coder` 只递增版本号，不改文件。所以虽然 debugger 每次都能正确检测失败，但回环中没有真正的"修 bug → 重写文件 → 再测"端到端流程。

- **建议**: 在场景 7 中让 dummy coder 修复测试文件（把 `assert fib(5) == 99` 改成 `assert fib(5) == 5`），验证完整的 fix-and-retest 回环。

## 4. 设计层观察

### 4.1 debugger 绕过 ToolRuntime 是架构张力

debugger 不能使用 ToolRuntime.run_cmd 的根本原因是 ToolRuntime 强制在 `self.workdir`（`{root}/af-{thread_id}/`）下执行命令，而 debugger 需要在 coder 创建的 workdir 下跑 pytest。这是两个不同的 workdir 概念冲突：

- **ToolRuntime workdir**: 线程级沙箱，ToolRuntime 自己管理
- **Coder workdir**: 项目级工作目录，跨节点共享

当前 debugger 通过绕过 ToolRuntime 解决这个冲突，但代价是丢失安全机制。长期方案应该是让 ToolRuntime 支持 `cwd` 参数或让 `run_cmd` 的安全检查独立于 workdir 概念。

### 4.2 lambda 闭包模式设计良好

coder 中 `fn=lambda p=file_path, c=code: (...)[1]` 使用默认参数在定义时捕获值，这是正确的 Python 模式，避免了常见的闭包延迟绑定陷阱。ctx.activity 的缓存机制与这个模式配合良好——首次执行时 fn() 运行并写文件，后续命中缓存时返回缓存值不重复写。

### 4.3 `workdir_explicit` 守卫设计清晰

coder 只在 `workdir_explicit=True` 时写 `artifacts`/`workdir` 到 state，这正确地将新旧场景隔离开。旧场景（1-5）不受新字段污染，新场景（6-7）获得完整数据。

## 5. 亮点

1. **测试覆盖扎实**：13 个测试用例（7 coder + 6 debugger），断言全部严格，无假绿色。`test_debugger_loop_max_steps` 验证了 max_steps 兜底机制。
2. **lambda 闭包正确**：`fn=lambda p=file_path, c=code: ...` 使用默认参数捕获值，避免了 Python 经典闭包陷阱。
3. **旧场景兼容干净**：`workdir_explicit` 守卫 + legacy_tasks fallback 确保 scenario 1-5 不受影响。
4. **activity 缓存回环安全**：缓存键含 step，回环中不同 step 不会撞缓存，设计正确。
5. **注释诚实**：debugger 中明确注释了为什么绕过 ToolRuntime，不隐瞒设计取舍。

## 6. 总评

- **总问题数**: P0: 3 / P1: 3 / P2: 5
- **整体评价**: 核心逻辑正确，回环缓存安全，旧场景兼容干净；但存在 1 个正则 bug（影响 class-based 测试）和 2 个安全问题（shell 注入 + 安全框架绕过）。
- **建议**: **有条件通过** — 修复 3 个 P0 后合并。P1 可在后续迭代处理。

### 必须修复（P0）

| # | 问题 | 文件:行 | 修复工作量 |
|---|------|---------|-----------|
| 1.1 | FAILED 正则无法解析 `Class::method` | `nodes.py:285` | 1 行正则修改 |
| 1.2 | debugger 绕过全部安全机制 | `nodes.py:243-245` | ~10 行加安全检查 |
| 1.3 | test_files shell 注入 | `nodes.py:237-238` | 1 行 `shlex.join` |

### 建议修复（P1）

| # | 问题 | 修复工作量 |
|---|------|-----------|
| 2.1 | pytest 收集失败静默误报 | ~5 行 |
| 2.2 | pytest 不可用行为不当 | ~5 行 |
| 2.3 | coder 临时目录泄漏 | ~5 行 try/finally |
