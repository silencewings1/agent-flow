# P1 Wave 1 CR 审查记录（2026-06-17）

> 范围：P1-1 (`7385bbf`)、P1-2 (`3f39738`)、P1-5 (`ef72eee`) 三个 feature 的合并 commit。
> 方法：fresh-eyes 独立审查，跑测试 + 读代码 + 对抗性 fuzz + 复现 PM 怀疑点。

## 0. 测试通过情况

| 套件 | 结果 | 备注 |
|------|------|------|
| test_invariants | ✅ | 2/2 |
| test_activity | ✅ | 7/7 + 3/3 tool_calls（无回归） |
| test_graph | ✅ | 17/17 |
| test_planner | ✅ | 11/11（含兼容性 test_full_pipeline_demo_compatible） |
| test_review | ✅ | 6/6 |
| test_tools | ✅ | 14/14 |
| demo.py | ✅ | 5/5 场景，状态正常中断在 human_review |

**所有测试表面全绿**。但对抗性 fuzz 暴露了多处真实问题（见下文）。

---

## 1. 严重问题（P0：必须修复，影响正确性或安全）

### 1.1 `run_cmd` 白名单可被 `python3 -c '...'` 完全绕过（沙箱声明虚高）

- **文件**: `agentflow/tools.py:175-221`（`run_cmd`）
- **现象**: 白名单只校验 `cmd` 第一个 token，但 `shell=True` 模式下 `python3 -c '...'` 可以执行任意 Python 代码：
  - 读 `/etc/passwd`：`python3 -c 'print(open("/etc/passwd").read())'` → 成功读到
  - 删文件：`python3 -c 'import os; os.remove("/tmp/agentflow_sentinel_test.txt")'` → 退出码 0，文件被删
  - 删目录：`python3 -c 'import shutil; shutil.rmtree("/tmp/agentflow_sentinel_dir")'` → 退出码 0，目录被删
  - 网络：`python3 -c 'import urllib.request; print(urllib.request.urlopen("http://example.com").status)'` → 输出 `200`
  - 同理 `python3 -c` 还能 fork 进程、读 SSH key、启动 `http.server` 等
- **复现**:
  ```python
  rt = ToolRuntime("t", root="/tmp")
  rt.run_cmd("python3 -c 'import os; os.remove(\"/etc/foo\")'")  # 不抛异常
  ```
- **建议**:
  1. **短期**（最低限度）：把 `python3` 和 `python` 从白名单移除；只保留 `pytest` / `cat` / `ls` / `git diff` 这类"参数可控"的命令
  2. **中期**：在 `run_cmd` 里加一个 re 扫描 `cmd` 是否含 `python3?\s+-c` / `python3?\s+-m` / `eval\s*\(` / `\$\(` 等模式，发现就拒绝
  3. **长期**：接 Docker / gVisor 真沙箱；或采用 `subprocess.run([...], shell=False)` 把命令拆分，对每段单独做白名单
- **严重程度理由**: 文档明文承诺「沙箱隔离」、「防止 LLM 误调 `rm -rf`」，但**当前实现连读取 `/etc/passwd` 都挡不住**。一旦接入恶意 / 受 prompt 注入影响的真实 LLM，整个宿主机文件系统可读可改。

### 1.2 `run_cmd` `cat`/`ls`/`python3` 不限路径，可读宿主任意文件

- **文件**: `agentflow/tools.py:175-221`
- **现象**: 即使不靠 `python3 -c` 注入，`cat /etc/passwd`、`ls /etc`、`cat -n /etc/passwd` 全部返回成功：
  ```
  cat /etc/passwd: exit_code=0, stdout='##\n# User Database\n...'
  ls /etc:        exit_code=0, stdout='afpovertcp.cfg\n...'
  ```
  `_check_no_dotdot` 只检查 `..`，不阻止绝对路径。`_resolve_within_workdir` 是给 `read_file/write_file` 用的，**`run_cmd` 完全没接 workdir 沙箱**（只 `cwd=self.workdir`，但 `cat /etc/passwd` 走绝对路径照样能读）。
- **复现**:
  ```python
  rt.run_cmd("cat /etc/passwd")  # 0 异常，文件被读
  ```
- **建议**: `run_cmd` 内应该对 `cmd` 解析出每个路径 token（剥离引号后），用 `_resolve_within_workdir` 校验路径全部落在 workdir 内；或者干脆禁用 `cat`/`ls` 的绝对路径。
- **严重程度理由**: `run_cmd` 是 P1-1 工具集中**最危险的方法**，计划用于 P1-4 debugger 跑 pytest。如果 LLM 生成的 cmd 形如 `cat /etc/passwd` 会被静默接受，无任何告警。

### 1.3 `ctx.tool()` 的 `key` 行为：同 key 撞缓存且无告警

- **文件**: `agentflow/graph.py:95-107`（`NodeContext.tool`）
- **现象**: `full_key = f"tool:{name}:{key}" if key else f"tool:{name}"` — 当 `key` 非空字符串/非 None 时，**缓存条目**是 `tool:<name>:<key>`。问题是：
  1. 同一个 `key` 值（如 `key="a"`）给不同文件 / 不同参数使用时，**第二次调用直接命中第一次的缓存**，返回错误结果。**测试 `test_tool_key_disambiguates_multiple_calls` 没有覆盖这种情况**。
  2. `key` 形如 `""` 和 `key=None` 都被当作"无 key"（`if key` 的 falsy 行为），与"显式不传 key"行为相同 — 这是隐式的、不在 docstring 里。
  3. `key` 含 `:` `/` `\` 等特殊字符时不会报错，缓存键就直接是这种字符串 — 不会破坏什么（cache key 是任意字符串），但与未来的"路径化 key"约定不符。
- **复现**:
  ```python
  # 同 key 不同文件 → 第二次返回第一次的结果
  r1 = ctx.tool("read_file", key="a", fn=lambda: read("a.txt"))  # 返回 "A"
  r2 = ctx.tool("read_file", key="a", fn=lambda: read("b.txt"))  # 返回 "A"（错了！应为 "B"）
  ```
  我已实测确认：`r1='content A', r2='content A'`。
- **建议**:
  1. 修复 `test_tool_key_disambiguates_multiple_calls`：增加"同 key + 不同 fn → 第二次仍执行"用例
  2. 考虑把 `key` 的命名改成更明确的 `disambiguator`，docstring 写清楚"key 是用来区分同一节点同 step 内同名工具的多次调用，**不是用来传参标识**"
  3. （可选）`key=None` 和 `key=""` 给出 `ValueError`，强迫调用方显式选择
- **严重程度理由**: 这是 PM 修复后留下的硬性要求（P1-1 fix `3cec3ba` 注释里写："P1-3 真实 Coder 的硬性要求"），但修复不完整。Wave 2 P1-3 上线时一定会踩到。

### 1.4 P0 时代遗留的 `_get_source` 函数被定义两次（PM 怀疑已确认）

- **文件**: `agentflow/graph.py:58-66` 和 `agentflow/graph.py:69-77`
- **现象**: 两个 `_get_source` 函数定义**完全相同**（import inspect、try/except、textwrap.dedent 兜底），Python 后定义覆盖前定义，因此**功能上没坏**：
  ```
  module-level _get_source: <function _get_source at 0x10992a2a0>
  Number of _get_source FunctionDef in AST: 2
    - line 58, col 0
    - line 69, col 0
  ```
- **历史溯源**: 这个 bug 是 P0-2 (`cae8996`) 引入的 — 在那个 commit 里 `def _get_source` 出现了 2 次。后续 P0-1 / P0-3 合并没有去重。P1-1 (`30ba1e2`) 也没动它，P1-1 fix (`3cec3ba`) 也没动它。P0 时代的 CR 报告（`docs/review-notes.md`）**未发现**这个重复。
- **建议**: 删除一个（保留 line 58 的更早定义，或保留 line 69 的更近定义都行）。最简单的修法：把 line 57-66 整段删掉。
- **严重程度理由**: 当前没造成 bug，但：
  1. 是明显的"开发流程缺陷"信号 — P0 的两次 CR 都没发现这个一行式重复
  2. 未来如果有人只改其中一份的 fallback 行为（比如改用 `inspect.getsource(inspect.unwrap(fn))`），会制造**静默不一致**
  3. Python `SyntaxWarning: redefinition of unused name` 在严格模式下会告警
  4. 降低代码可读性 — 阅读者会困惑哪个是真的

---

## 2. 一般问题（P1：建议修复，影响质量/可维护性）

### 2.1 `apply_patch` 对空 diff 静默"成功"，可能制造大量空文件

- **文件**: `agentflow/tools.py:116-171`（`apply_patch`）
- **现象**: `apply_patch('evil.py', '')` 不会抛异常，返回 `{"path": "evil.py", "applied": True, "hunks": 0}`，并创建了**空文件** `evil.py`。`patch` 命令对空 stdin 返回 0，所以我们的 dry-run 和真跑都"通过"。
- **复现**:
  ```python
  rt.apply_patch("malicious.py", "")  # 不抛异常
  os.path.exists("malicious.py")  # True, size 0
  ```
- **建议**: 在 `apply_patch` 开头加：
  ```python
  if not unified_diff or not unified_diff.strip():
      raise ValueError("apply_patch 需要非空 unified_diff")
  ```
  或在 dry-run 之后检查 `real.stdout` 是否真有 `applied` 字样。
- **严重程度**: P1 — 不是安全问题（workdir 是隔离的），但是 LLM 反复调 `apply_patch("x.py", "")` 会污染 workdir，掩盖实际写入。Wave 2 P1-3 coder 写文件时容易踩到。

### 2.2 `planner` 节点的确定性任务拆分是死代码

- **文件**: `agentflow/nodes.py:46-54`（`planner`）
- **现象**: 节点确实按中文/英文逗号 split 出 `task_titles`，构造 `tasks_seed`（如 `"实现登录接口，加上单元测试，写好文档"` → 3 个 seed task），但下游 `parse_plan_from_llm` 第三层 fallback 总是返回**1 个 task**（即 `requirement` 本身）。`plan.tasks` 永远非空（fallback 兜底），所以 `if not plan.tasks: plan.tasks = tasks_seed` 这行**永远不执行**。
- **复现**:
  ```python
  planner({"requirement": "做a，do b, 还有 c"}, ctx)
  # state["tasks"] = ["t1"]   ← 应该是 3 个 task
  ```
- **建议**:
  - 选项 A：删掉 `tasks_seed` 代码（明确放弃 mock 下的拆分）
  - 选项 B：调整 fallback 顺序 — 当 LLM 输出不可解析时，**优先用 `tasks_seed`** 而非 `_mock_plan(requirement)` 单 task
- **严重程度**: P1 — 不影响 demo 跑通（P1-5 的 demo 关注点不在 task 数量），但 P1-3 coder 写多文件时 task 数量决定 coder 写几个文件，**直接影响 P1-3 demo 的"产物数"**。

### 2.3 `parse_plan_from_llm` 多 JSON 块时只取第一个且不尝试后续

- **文件**: `agentflow/plan.py:69`（`_JSON_BLOCK_RE`）和 `agentflow/plan.py:119-133`
- **现象**: 正则 `_JSON_BLOCK_RE.search(text)` 用的是 `search`（不是 `findall`），所以只取第一个匹配块。如果 LLM 输出了多个 ```json``` 块（很常见的"先解释后给代码"风格），**且第一个块 validate 失败**，整个 pipeline 走 mock fallback，**不会尝试第二个块**。
- **复现**:
  ```python
  raw = """
  ```json
  {"summary": "first", "tasks": []}  ← 故意空，validate 失败
  ```
  ```json
  {"summary": "good", "tasks": [{"id": "t1", "title": "x"}]}
  ```
  """
  plan = parse_plan_from_llm(raw, "req")
  # plan.summary = "实现 req" ← 走了 mock，没用第二个块
  ```
- **建议**: 用 `findall` 取所有块，依次尝试直到 validate 通过：
  ```python
  for m in _JSON_BLOCK_RE.finditer(text):
      block = m.group(1).strip()
      try:
          obj = json.loads(block)
          plan = _coerce_to_plan(obj)
          if plan is not None and not plan.validate():
              return plan
      except (json.JSONDecodeError, ...):
          continue
  ```
- **严重程度**: P1 — 真实 LLM 经常输出"先解释 → 再给 JSON"的格式，目前完全放弃第二个块会失去合理的 plan。当前 demo 看不到影响（因为 mock LLM 根本走不到正则层）。

### 2.4 `ctx.tool()` 静默忽略未知 kwargs，可能掩盖 typo bug

- **文件**: `agentflow/graph.py:95-107`
- **现象**: `ctx.tool("read_file", fn=myfn, input_sumary="A")`（注意是 `input_sumary` 不是 `input_summary`）— typo 的 kwarg 被静默忽略，tool_call 表里 `input_summary` 是空串，**没有任何告警**。
- **复现**:
  ```python
  cp = Checkpointer()
  app = ...
  r = app.invoke({}, thread_id="kwarg")
  # tool_calls 表中 input_summary=''  ← 期望 'A'，但 typo 被吞
  ```
- **建议**: `kwargs.pop("input_summary", "")` 之后，检查 `kwargs` 是否还有未消费的 key，若有就 `print(f"[ctx.tool] WARN: 忽略未知 kwargs: {list(kwargs)}")`（与 plan.py 的 `[plan] WARN` 风格一致）。或更严格 — `raise TypeError`。
- **严重程度**: P1 — 当前 demo 不传 input_summary，所以无影响。但 P1-3 真实 Coder 计划传 `input_summary=task_title`，typo 会导致审计日志失真。

### 2.5 `parse_plan_from_llm` 走 mock fallback 时丢失 LLM 的 partial 信息

- **文件**: `agentflow/plan.py:136-137`（mock fallback） + `agentflow/nodes.py:78-83`（planner）
- **现象**: 当 LLM 输出无法解析时，fallback 返回 `_mock_plan(requirement)`，但 LLM 原文本里的"澄清问题"等可能存在的部分信息被**完全丢弃**。比如 LLM 输出 `"这段需求不明确：是 X 还是 Y？"` — 这本身是个 `clarifying_questions`，但因为不是 JSON 格式，整段被丢。
- **建议**: 在第 2 层失败但第 3 层走 mock 之前，**尝试用关键词提取**（如 `？` 结尾的句子 → `clarifying_questions`），作为 mock 的补充信息。可选改进，不阻塞。
- **严重程度**: P2 — 设计改进，不算 bug。

### 2.6 `Plan.validate()` 不检查 `details` 字段类型

- **文件**: `agentflow/plan.py:49-64`
- **现象**: validate 只检查 `id` / `title` / `summary` / `tasks` 非空，**不检查 `details` 字段的类型**。恶意 LLM 输出 `{"id": "t1", "title": "x", "details": [[[[[...100 层嵌套...]]]]}` 会被接受，`plan.to_dict()` 会原样传给 coder，coder 在写 prompt 时把 100 层 list 拼到字符串里可能很慢。
- **复现**:
  ```python
  raw = '{"summary":"x","tasks":[{"id":"t1","title":"y","details":' + '[' * 100 + ']' * 100 + '}]}'
  parse_plan_from_llm(raw, "req").tasks  # 接受，不警告
  ```
- **建议**: validate 加 `if not isinstance(t.get("details"), str): errs.append("details 应为 str")`。P1-2 文档说 `details` 是 str。

### 2.7 `human_review` `decision` 类型处理不一致：`bare bool` 与 `dict` 行为不同

- **文件**: `agentflow/nodes.py:161-174`（`human_review`）
- **现象**:
  - `decision={"approve": True}` → `approved=True, human_review_decision={"approve": True}` ✓
  - `decision={"approve": False}` → `approved=False, human_review_decision={"approve": False}` ✓
  - `decision=True` (bare bool) → `approved=True, human_review_decision={"approve": True}` ✓
  - `decision=False` (bare bool) → `approved=False, human_review_decision={"approve": False}` ✓
  - `decision=None` → `approved=False, human_review_decision={"approve": False}` (因为 `bool(None)=False`)
  - `decision="yes"` → `approved=True, human_review_decision={"approve": True}` (因为 `bool("yes")=True`)
- **建议**: 当前行为可接受（且与 LangGraph 风格一致），但应在 docstring 里写清楚"resume 值可以是 `bool` 或 `{"approve": bool}`"。当前 docstring 一字未提。
- **严重程度**: P2 — 不算 bug，但文档不完整。

---

## 3. 细节问题（P2：可选，不影响功能）

### 3.1 `test_planner_node_returns_structured_state` 有死代码

- **文件**: `test/test_planner.py:135-150`
- **现象**: 测试里 `reg = LLMRegistry(...)` 在 line 135 定义一次，line 142 又被覆盖。第一次定义完全没用。
- **建议**: 删除 line 135-140。

### 3.2 `pass_at_version=None` 会让 debugger 抛 `TypeError`

- **文件**: `agentflow/nodes.py:125`
- **现象**: `passed = version >= state.get("pass_at_version", 3)` — 当 `state` 显式含 `"pass_at_version": None` 时，default 不触发，拿到 `None`；`1 >= None` 在 Python 3 抛 `TypeError`。当前 demo 都用 `pass_at_version=3` 或 `1`，不会触发；但外部调用方如果传 `None` 会让 pipeline 失败而非降级。
- **建议**: 改为 `state.get("pass_at_version") or 3`（用 truthy 兜底），或显式 `isinstance` 检查。

### 3.3 `apply_patch` 失败时仍创建目标空文件

- **文件**: `agentflow/tools.py:124-126`
- **现象**: `apply_patch` 在 `os.path.exists(full)` 为假时先 `open(full, "wb").close()` 创建空文件，再走 patch 校验。如果 patch 失败，**空文件已经留下了**（这是 `git apply` 的标准行为，但 P1 没说清楚）。如果 patch 成功但用户期望"创建空文件"会被无声地"已存在"骗过。
- **建议**: 在 docstring 里写明"目标文件不存在时会先创建空文件"。

### 3.4 `apply_patch` 的 `hunks` 计数是估算

- **文件**: `agentflow/tools.py:169-170`
- **现象**: `hunks = sum(1 for line in unified_diff.splitlines() if line.startswith("@@ "))` — 这是源码计数，但真实 patch 应用了多少 hunk 由 `patch` 命令决定（可能有 malformed hunk 被丢弃）。返回值 `hunks` 不一定等于实际应用的 hunk 数。
- **建议**: 从 `real.stdout` 解析 `patch` 命令输出的 `@@ -X,Y +X,Z @@` 行数，作为更准确的 `hunks`。

### 3.5 `_check_no_dotdot` 对 unicode `..` / 全角 `．` 不处理

- **文件**: `agentflow/tools.py:41-56`
- **现象**: 正则 `r"(^|/)\.\.($|/)"` 只匹配 ASCII 的 `..`；中文/全角变体（如 `．．`）或 unicode 规范化后的 `..`（U+002E U+002E with combining char）不会被识别。
- **严重程度**: 极低 — 实际攻击面有限。**但是 PM 专门问了这个**，所以我跑了测试确认。结论：当前实现对 `..` 字符串字面量有效，对 unicode bypass **未防御**。

### 3.6 `apply_patch` 没限定 patch 大小

- **文件**: `agentflow/tools.py:116-171`
- **现象**: 1M 行的 diff 会被读入内存，patch 进程会试图吃光内存。当前没有 limit 校验。
- **建议**: 在 dry-run 之前加 `if len(unified_diff) > 1_000_000: raise ValueError(...)`。

### 3.7 `ctx.tool()` 的 docstring 没提 `key` 是要 disambiguate"同名工具多次调用"

- **文件**: `agentflow/graph.py:95-107`
- **现象**: docstring 写"若同节点同 step 对同一工具调多次（参数不同），应传 key="<disambiguator>" 让每次调用有独立缓存条目" — 这句是对的，但没说明**用同样的 key + 不同的 fn 仍会撞缓存**（见 1.3）。

### 3.8 `Plan.from_dict` 把 acceptance_criteria 全部 str() 化，丢失类型

- **文件**: `agentflow/plan.py:35-47`
- **现象**: `acceptance_criteria=[str(x) for x in (d.get("acceptance_criteria") or [])]` — 即使 LLM 输出 `["yes", 80, true]`，会被强转成 `["yes", "80", "True"]`，类型信息丢失。
- **建议**: 接受 LLM 的类型，但 spec 说 criteria 是 str list，所以这算"按 spec 强转"，无 bug。

---

## 4. 设计层观察

### 4.1 `run_cmd` 的"白名单"是 theater security

白名单把 `python3` 放进 allowlist，但 `python3 -c` 是图灵完备的。一旦 LLM（或被 prompt 注入的 LLM）能写任意 Python，host 文件系统、进程、网络就全暴露。当前 `run_cmd` 的实际语义是"防止 LLM 误调 `rm -rf`" — 但 `rm -rf` 真的没被白名单挡住（`python3 -c 'import os; os.system("rm -rf /")'` 反而能跑）。

**设计选项**：
- **A. 真沙箱** — Docker 容器 / gVisor / bubblewrap。Wave 2 工作量 +1 天
- **B. 命令层 ASR** — 用 `shlex.split` 把 cmd 拆成 argv，对每段做白名单 + 路径校验。短期可做
- **C. 限制白名单** — 只允许 `pytest` 和 `git` 这种**argv 完全可控**的命令。短期可做
- **D. 接受现实** — 在 docstring 里写"ToolRuntime 是工具便利层，**不是**安全沙箱；生产环境需要接 OS 级沙箱"

我建议 A > C > B > D。当前 P1-1 走在 D 路径上但 docstring 没明说，文档与实现有差距。

### 4.2 P1-2 文档说"任务拆分为 3 个 task"是确定性 split，但 P1-2 实际实现完全没做这件事

`docs/plan-p1.md:283-304` 描述 P1-3 coder "对每个 task 写一个文件" — 这隐含 plan 真的把需求拆成多个 task。但 P1-2 的 `parse_plan_from_llm` mock fallback 总是返回 1 task，所以 P1-3 写多少文件完全取决于 LLM 是否配合。

实际数据：requirement `"实现登录接口，加上单元测试，写好文档"` → 计划 1 task → coder 写 1 个文件。这与 P1-3 的"产物列表"设计不匹配。

### 4.3 `ctx.tool()` 的 kwargs 设计是 "silently ignore" — 业界反模式

Python 函数式 API 一般用 `**kwargs` 接可选项，但**对未知 kwargs 应该 raise TypeError**（如 `functools.lru_cache` 的行为）。当前实现静默忽略 typo 是"宽松 → 难调试"的反模式。PEP 570 / Python 官方风格都不推荐。

### 4.4 `Plan.validate()` 与 `parse_plan_from_llm` 的契约是"validate 失败 = 用 mock"，但 spec 是"validate 失败 = 拒绝"

`docs/plan-p1.md:160-162` 说"解析后 `plan.validate()` 必须通过；不通过则 fallback 到 mock" — 这是 fallback 策略，可接受。但 `Plan.validate()` 的 API 文档（`plan.py:50`）说"返回错误信息列表。空列表 = 合法" — 这暗示调用方**应该**自己处理错误。当前 `parse_plan_from_llm` 内部把 validate 错误吞了，**调用方拿不到 validate 错误**。

如果 Wave 2 真实 Coder 想"如果 LLM 给的计划不达标就退回重试"，当前 API 拿不到错误信息。

### 4.5 `NodeContext.tool()` 的 key 设计方向与 cache invariant 矛盾

`ctx.tool("read_file", key="a", fn=lambda: read("a.txt"))` 和 `ctx.tool("read_file", key="a", fn=lambda: read("b.txt"))` 应该**不**是同一条缓存（因为 fn 不同），但当前实现是同一条。这是 cache key 设计的根本问题 — key 不能 disambiguate "the tool is the same but the inputs differ"。

更好的设计是 cache key 自动包含 `fn` 的 hash / 源码位置 / 参数指纹。但这是"通用 activity cache"的设计变更，影响面大。

---

## 5. 亮点

- **P1-2 三层 fallback 设计**：直接 JSON → 正则代码块 → mock，永不抛异常打断 pipeline，CI 不需要 API key。✓
- **P1-5 review 拆分清晰**：`ai_review` 不中断（已 grep 确认只 `human_review` 调 `ctx.interrupt`），`state["ai_review"]` 与 `state["approved"]` 分开存储。✓
- **P1-1 `apply_patch` dry-run 校验**：先 `--dry-run` 确认 hunk 匹配，再真跑。失败抛 `RuntimeError` 带 stderr 摘要，错误信息友好。✓
- **P1-1 路径安全**：`read_file/write_file/list_dir` 用 `os.path.realpath` + workdir 前缀校验，对符号链接攻击免疫（已实测确认）。✓
- **P1-1 `cleanup()` 幂等**：连续调 `cleanup()` 不报错，删不存在的目录也不报错。✓
- **P1-1 `git_diff` 优雅退化**：非 git 仓库返回 `""` 而不是抛异常。✓
- **P1-2 任务 ID 自动分配**：`planner` 在 plan 解析失败时回填 id/title，**保证下游不 IndexError**。✓（与 2.2 的 seed 死代码问题不冲突）
- **P1-2 中文/英文/分号都支持**：`raw.replace("，", ",").replace("；", ";").replace(";", ",")` 三个 replace 全到位。✓
- **P1-5 state 字段名变更彻底**：`review_note` → `human_review_decision` 全文件无残留（grep 确认）。✓
- **测试覆盖规划清晰**：P1-1 13 用例 + P1-2 7 用例 + P1-5 6 用例 = 26 个新测试全过。✓
- **demo.py 完整跑通**：5 场景，含 reject/approve 双路径，checkpoint 历史可打印。✓

---

## 6. 总评

- **总问题数**: P0 **4** 个 / P1 **7** 个 / P2 **7** 个
- **整体评价**: **有条件通过** — 功能与测试覆盖合格，但 `run_cmd` 的"沙箱"声明与实现严重不符（**这是文档-实现差距**），`ctx.tool()` 的 key 行为 PM 已怀疑但修复不完整，`_get_source` 重复定义是开发流程缺陷。建议 Wave 1 不能算"完全通过"，需要在 P1-3 启动前修 P0 问题。
- **建议**: **有条件通过**（修 4 个 P0 后通过；P1 问题可以列入技术债但建议本轮一起修）

### 必须修的 4 个 P0：

1. **1.1 + 1.2**（合并处理）：`run_cmd` 的"沙箱"声明需要重新审视 — 要么补上路径校验逻辑，要么把 `python3`/`python`/`cat` 从白名单拿掉，要么在 docstring 里诚实写明"不是真沙箱"。**最关键的是文档与实现要一致**。
2. **1.3**：`ctx.tool()` 的 key 行为 — 改 docstring 写明 key 撞缓存的后果，并扩展 `test_tool_key_disambiguates_multiple_calls` 加 "同 key + 不同 fn" 反向用例。
3. **1.4**：删掉 `agentflow/graph.py:58-66` 的重复 `_get_source`。

### 最严重的 1-2 个问题摘要：

1. **`run_cmd` 沙箱声明虚高（1.1 + 1.2）**：白名单完全可被 `python3 -c '...'` 绕过，`cat /etc/passwd` 也能直读。这与 `docs/plan-p1.md:72` "沙箱隔离" 承诺不符，是 Wave 2 接入真实 LLM 后**第一个会爆的安全问题**。

2. **`_get_source` 重复定义（1.4）**：P0 时代的 CR 没发现这个一行式重复，**反映 CR 流程有盲点**。如果这 3 个 P1 feature 的 CR 报告也用类似方法审，这种"重复定义但没影响"的问题仍可能被漏。建议后续 CR 加一条"用 AST 工具扫一遍重复定义"。

### 给 PM 的决策建议：

- **接受（直接合）**：可以接受，但 Wave 2 启动前必须把 4 个 P0 修掉
- **开 fix branch（推荐）**：开一个 `fix/p1-run-cmd-sandbox` + 修 `_get_source` + 修 `ctx.tool()` key 的小 fix branch，PM 验证后并入 master
- **打回 P1-1 Dev**：如果希望"沙箱"承诺被认真对待，可以打回 P1-1 Dev 重做 `run_cmd` 的安全层

我倾向 **开 fix branch**，因为：
- P1-1 / P1-2 / P1-5 三个 feature 的功能与测试都达标
- 4 个 P0 里有 3 个是"小改即可"（删 9 行重复 / 改 docstring / 改测试断言）
- 1 个（run_cmd 沙箱）是设计决策 — 需要 PM 决定方向 A/B/C/D
