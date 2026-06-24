# CR Backlog Round 1 事后独立审查记录（2026-06-18）

> 范围：commit `9756063`（Merge branch 'fix/cr-backlog-round1'）相对父 commit `3a0fe3e` 的改动。
> 性质：**事后独立审查** — PM 跳过了 CR 步骤直接合并，现追溯审查。
> 方法：fresh-eyes 独立审查，跑全部 9 套测试 + demo.py + 11 项对抗性验证。

## 0. 测试通过情况

| 套件 | 结果 | 备注 |
|------|------|------|
| test_invariants | PASS | 2/2 |
| test_graph | PASS | 17/17 |
| test_planner | PASS | 全部通过 |
| test_tools | PASS | 20/20 |
| test_review | PASS | 6/6 |
| test_activity | PASS | 全部通过 |
| test_coder | PASS | 7/7 |
| test_debugger | PASS | 11/11（含新增 5 个 regex/fallback 测试） |
| test_py37_compat | PASS | 10/10 |
| demo.py | PASS | 7/7 场景全部完成 |

**所有测试全绿，demo 7 场景全过。**

---

## 1. 严重问题（P0：必须修复，影响正确性）

（无 P0 问题。）

---

## 2. 一般问题（P1：建议修复，影响质量/可维护性）

### 2.1 PEP 604 联合类型正则漏检 `-> int | str:` 返回类型注解

- **文件**: `test/test_py37_compat.py:250`（正则）
- **现象**: 正则 `r"[:)]\s*[\w\[\],. ]+\s*\|\s*[\w\[\],. ]+"` 要求 `:` 或 `)` 在类型表达式**前面**。但 Python 返回类型注解的常见写法 `def foo() -> int | str:` 中，`:` 在类型表达式**后面**，导致正则完全不匹配。

- **复现**:
  ```python
  import re
  pat = re.compile(r"[:)]\s*[\w\[\],. ]+\s*\|\s*[\w\[\],. ]+")
  pat.search("def foo() -> int | str:")  # → None（漏检！）
  pat.search("def foo() -> int | None:")  # → None（漏检！）
  ```

- **漏检的典型写法**（全部在 `->` 返回类型位置）:
  - `def foo() -> int | str:`
  - `def foo() -> int | None:`
  - `def foo() -> str | None:`

- **已正确检测的写法**:
  - `def foo(x: int | str) -> None:` — 参数注解（`:` 在 `|` 前）
  - `x: str | None = None` — 变量注解（`:` 在 `|` 前）
  - `x: Optional[int | str] = None` — 嵌套 Optional

- **影响范围**: 当前代码库没有 PEP 604 返回类型注解，所以测试恰好通过。但这是"假绿"——**未来任何人**写 `def foo() -> int | str:` 都不会被检测。与修复前的正则比，新正则修复了"小写类型名"的问题，但**引入了新的漏检**。

- **建议**: 在正则前增加对 `->` 返回类型的预处理——把 `->` 替换为 `:` 后再匹配，或单独加一条正则 `r"->\s*[\w\[\],. ]+\s*\|\s*[\w\[\],. ]+"`。

- **严重程度理由**: 这是质量门测试的假绿——测试在跑但不真正检测它声称检测的东西。`-> int | None` 是 Python 3.10+ 最常见的 PEP 604 写法之一。

### 2.2 PEP 585 AST 检测对多行返回类型 `-> Optional[tuple[...]]` 存在误报风险

- **文件**: `test/test_py37_compat.py:194-197`（启发式）
- **现象**: 对于含 `from __future__ import annotations` 的文件，AST 检测用启发式 `line.index(':') < line.index('[')` 判断是否在注解上下文。当 `:` 与 `[` 在同一行但 `:` 在 `[` 之后时（如 `def foo() -> Optional[tuple[str, ...]]:`），会**误报**。

- **复现**:
  ```python
  src = '''from __future__ import annotations
  def foo() -> Optional[tuple[str, ...]]:
      return {}
  '''
  # tuple[...] 会被错误标记为违反 PEP 585
  # 实际上 from __future__ import annotations 下所有注解都是安全的
  ```

- **实际影响**: 当前代码库的 `agentflow/checkpoint.py:113` 有 `Optional[tuple[Any, str]]`，但因为同一行上 `activity_key: str` 中的 `:` 碰巧在 `[` 之前，所以**恰好没触发**。但这是巧合，不是设计正确。

- **建议**: 用更精确的 AST 上下文判断——检查 Subscript 节点的祖先链中是否有 `ast.AnnAssign`、`ast.arg`（在 `ast.arguments` 中）、或 `ast.FunctionDef.returns`，而不是靠行内字符位置启发式。

- **严重程度理由**: P1 — 当前不触发，但启发式脆弱，未来代码重构可能误报。误报会直接导致 `test_no_py39_pep585` 失败，阻碍合并。

---

## 3. 细节问题（P2：可选，不影响功能）

### 3.1 `parse_plan_from_llm` 中 `import re as _re` 未使用

- **文件**: `agentflow/plan.py`（推测，未确认行号）
- **现象**: 修复 6 在函数体内 `import re` 使用了 `re.findall`，但模块顶部已有 `import re`。如果模块顶部没有 `import re`，那么 `re.findall` 的 `re` 来自函数内 import。检查发现模块顶部第 6 行有 `import re`，所以函数内 `re.findall` 使用的是模块级 `re`。无 bug。

- **实际确认**: 经检查 `plan.py` 顶部有 `import re`，函数内未重复 import。OK。

### 3.2 `_mock_plan` 兜底保留确认

- **文件**: `agentflow/plan.py:140-149`
- **现象**: 当 `tasks_seed` 为 `None`（旧调用方不传）时，第 3 层走 `_mock_plan(requirement)` — 兜底未被跳过。对抗测试确认：
  ```python
  parse_plan_from_llm('', 'test')  # → _mock_plan, tasks=1
  parse_plan_from_llm('', 'test', tasks_seed=[...])  # → tasks_seed, tasks=2
  ```
  行为正确。

### 3.3 `not plan.validate()` 条件语义确认

- **文件**: `agentflow/plan.py:114, 130`
- **现象**: 第 1 层和第 2 层的成功条件都是 `if plan is not None and not plan.validate()` — `Plan.validate()` 返回错误列表，空列表为合法。`not []` = `True`，所以 validate 通过时进入 return。语义正确。

### 3.4 finditer 循环中对 validate 失败但 plan 不为 None 的块正确跳过

- **文件**: `agentflow/plan.py:124-134`
- **现象**: 第 2 层循环中，当 `plan is not None` 但 `plan.validate()` 返回非空（即 validate 失败），进入 `else` 分支打 WARN 日志后 `continue`（隐式，因为循环继续下一个迭代）。对抗测试确认：
  - 第 1 块 validate 失败 → 打 WARN → 尝试第 2 块
  - 第 2 块 validate 通过 → return
  行为正确。

### 3.5 coder cleanup 下游安全确认

- **文件**: `agentflow/nodes.py:181-187`, `agentflow/nodes.py:205-221`
- **现象**: `workdir_explicit=False` 时 coder 返回的 `result` 不含 `workdir` 字段，且临时目录已被 rmtree。下游 debugger 通过 `state.get("workdir", "")` 取到空字符串，`not workdir` 触发 fallback 到旧 `pass_at_version` 行为（line 206）。确认无下游访问已清理目录的风险。

### 3.6 pytest 探测边界：pytest 存在但损坏

- **文件**: `agentflow/nodes.py:250-256`
- **现象**: 如果 pytest 存在但 `--version` 返回非零退出码（损坏的安装），`_pytest_available = _probe.returncode == 0` 会设为 `False`，正确 fallback 到 `pass_at_version`。如果 pytest 存在但 `--version` 超时，`TimeoutExpired` 被 catch，`_pytest_available = False`。所有三条路径都正确处理。

### 3.7 `verify_py37.sh` PYTHON37 环境变量支持

- **文件**: `scripts/verify_py37.sh:7-10`
- **现象**: 脚本现在先检查 `PYTHON37` 环境变量，再 fallback 到 `python3.7`/`python3.8`，找不到则 `exit 1`。对抗测试确认：shebang 为 `#!/usr/bin/env bash`，`PYTHON37` 优先，无 3.7/3.8 时明确退出。

---

## 4. 设计层观察

### 4.1 PEP 585 AST 检测的 `from __future__ import annotations` 处理是近似正确

AST 检测使用启发式 `line.index(':') < line.index('[')` 判断注解上下文。这在大多数情况下有效（函数参数 `x: list[int]`、变量注解 `items: dict[str, int]`），但有两个已知盲区：

1. **误报**：`def foo() -> Optional[tuple[str, ...]]:` — 返回类型中的 `:` 在 `[` 之后（见 P1 2.2）
2. **漏检**：单行多语句 `x = 1; y: list[int] = []` — `;` 后的 `:` 会被 `line.index(':')` 找到，但这是正确的

当前代码库恰好避开了这两个盲区。建议后续用 AST 祖先链判断替代字符位置启发式。

### 4.2 PEP 604 正则的渐进改进策略

旧正则只匹配大写类型名（`[A-Z]...`），新正则允许小写（`[\w...]`），且加了 `:` 和 `->` 的上下文守卫防止误报。改进方向正确，但 `-> int | str:` 模式被漏检（见 P1 2.1）。建议增加对 `->` 的预处理。

---

## 5. 亮点

- **修复 2 (planner seed task)**：`tasks_seed` 参数默认 `None`，旧调用方零影响。`parse_plan_from_llm` 第 3 层优先用 `tasks_seed`，保留了确定性拆分，mock 兜底不被跳过。对抗测试确认。
- **修复 3 (多 JSON 块 finditer)**：`search` → `finditer`，依次尝试直到 validate 通过。第 1 块失败不会放弃第 2 块。对抗测试确认。
- **修复 4 (details 类型检查)**：`"details" in t and not isinstance(t["details"], str)` — 只在 details 存在时检查类型，缺失的 details 不报错。对抗测试确认。
- **修复 5 (human_review docstring)**：明确写清楚 resume 值可以是 `bool`、`{"approve": bool}`、或其他 truthy/falsy 值。文档完整。
- **修复 6 (mock fallback partial 信息)**：用 `？` 结尾的句子提取 `clarifying_questions`，最多 5 个。对抗测试确认中文问句正确匹配。
- **修复 7 (debugger pytest 探测)**：`subprocess.run(["pytest", "--version"], ...)` 无 `shell=True`，安全。探测失败 → `_pytest_available = False` → 正确 fallback。
- **修复 8 (coder 临时目录清理)**：`workdir_explicit=False` 时 `shutil.rmtree(workdir, ignore_errors=True)` + 外层 `except Exception: pass`，双重保险。下游 debugger 通过 `not workdir` 守卫安全 fallback。
- **修复 9 (PEP 585 AST 检测)**：从 regex 改为 `ast.Subscript` + `ast.Name` 检测，比旧正则精确得多，不再漏检 `Optional[list[int]]` 等嵌套形式。`from __future__ import annotations` 文件正确豁免注解上下文。
- **修复 10 (verify_py37.sh)**：`PYTHON37` 环境变量 + `exit 1` 拒绝静默回退。脚本可靠性大幅提升。
- **向后兼容**：`parse_plan_from_llm` 的 `tasks_seed` 参数默认 `None`，所有旧调用方无需修改。

---

## 6. 总评

- **总问题数**: P0: 0 / P1: 2 / P2: 7（含设计层 2）
- **整体评价**: **通过** — 10 个修复全部正确实现，无 P0 问题。9 套测试 + demo.py 7 场景全过。发现 2 个 P1 问题均为测试工具体系的质量门盲区（PEP 604 正则漏检返回类型、PEP 585 AST 启发式可能误报），不影响生产代码正确性。
- **建议**: **接受**，P1 问题列入技术债，可在 Round 2 或后续迭代修复。

### 最严重的 1-2 个问题摘要：

1. **PEP 604 正则漏检 `-> int | str:`（P1 2.1）**：`test_no_py310_union_pipe` 的改进正则在修复"小写类型名漏检"的同时，引入了对 `->` 返回类型注解的漏检。`def foo() -> int | None:` 这种最常见的 PEP 604 写法无法被检测。当前代码库没有这种写法所以测试恰好通过，但质量门本身存在盲区。

2. **PEP 585 AST 启发式脆弱（P1 2.2）**：`line.index(':') < line.index('[')` 作为注解上下文的判断依据在返回类型 `-> Optional[tuple[...]]:` 这种场景下会误报。当前代码库碰巧避开了这个模式，但启发式本身不可靠。

### 给 PM 的决策建议：

- **直接接受**：10 个修复全部正确，生产代码无回归。P1 问题仅影响测试工具自身的覆盖度，不影响 agentflow 功能。
- **建议**：将 P1 2.1（PEP 604 正则改进）和 P1 2.2（PEP 585 AST 祖先链判断）列入 Round 2 修复清单。
