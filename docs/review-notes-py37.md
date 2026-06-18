# dev_py37 CR 审查记录（2026-06-18）

> 范围：`dev_py37` 单 commit `ac5af0f` 相对 master 的所有改动
> 目的：让 agentflow 在 Python 3.7 下能跑
> 方法：fresh-eyes 独立审查，跑测试 + grep/fuzz + AST 扫描 + 单独构造违规验证测试质量

## 0. 测试基线

| 套件 | 结果 | 备注 |
|------|------|------|
| test_invariants | ✅ | 2/2 |
| test_activity | ⚠️ flaky | 7/7 但 `test_tool_calls_logged` 偶发（duration_ms==0 时挂）——master 也存在，非本分支引入 |
| test_graph | ✅ | 17/17 |
| test_planner | ✅ | 全部通过 |
| test_review | ✅ | 6/6 |
| test_tools | ✅（在 3.14 下） | 20/20；**但在 3.7 下会 SyntaxError**（见 P0 #2.1） |
| test_coder | ✅ | 全部 |
| test_debugger | ✅ | 全部 |
| test_py37_compat | ⚠️ 假绿 | 10/10，但**没有真的检测到 `test/test_tools.py:178` 的 3.8+ f-string conversion**（见 P0 #2.2） |
| demo.py | ✅ | 7/7 场景 |
| 真实 3.7 跑测 | ❌ 不可验证 | 当前环境无 3.7/3.8，脚本静默回退到 3.14（见 P1 #3.1） |

**表面全绿，对抗性 fuzz 暴露 2 个 P0 缺陷 + 4 个 P1/P2 设计问题**。

---

## 1. 严重问题（P0：必须修复，影响正确性或安全）

### 1.1 `test/test_tools.py:178` 残留 3.8+ f-string conversion —— 在 Python 3.7 下 `SyntaxError`

- **文件**: `test/test_tools.py:178`
- **现象**:
  ```python
  assert result["stdout"].strip() == "1", f"stdout 应为 '1'，实际 {result['stdout']!r}"
  ```
  `f"...{result['stdout']!r}"` 用到了 f-string conversion（PEP 498 修订，3.8+）。
- **实际跑测影响**:
  - 在 3.14 下解析正常、运行正常（3.14 当然支持）
  - 在 3.7 下 `python3 test/test_tools.py` 会直接抛 `SyntaxError: f-string ... !r conversion`
  - 整个 `test_tools.py` 模块不可导入 → 20 个测试全 0 跑起来
- **遗漏原因**:
  - PM commit message 说"test/test_tools.py — 5 处 f-string conversion 修正"，但实际有 6 处
  - Dev 漏改了第 6 处
- **建议**:
  1. 把第 178 行 `f"stdout 应为 '1'，实际 {result['stdout']!r}"` 改为 `f"stdout 应为 '1'，实际 {repr(result['stdout'])}"`
  2. Dev 应当**重新 grep 一遍** `git grep -E '!r|!s|!a'` 整个仓库，确认无遗漏
  3. 建议把这条加到 `test_py37_compat.py` 的必检清单里（用更强的 AST/正则）
- **严重程度理由**: 这是 dev_py37 分支存在的根本目的；如果这一行漏改，整个分支的"支持 3.7"承诺就是空话。在 3.7 环境跑 `pytest test/test_tools.py` 会瞬间挂掉。
- **相关**: 配合 P0 #1.2（检测器失效）

### 1.2 `test_no_py38_fstring_conversion` 正则有 false negative —— 它没发现 #1.1

- **文件**: `test/test_py37_compat.py:128-144`
- **现象**:
  - 目标：扫描代码里的 3.8+ f-string conversion（`!r`/`!s`/`!a`）
  - 实际：在 3.14 下跑这条测试，**给出绿色 PASS**；但 `test/test_tools.py:178` 实际含 3.8+ f-string conversion
- **正则源码**:
  ```python
  pat = re.compile(r"""f['"][^'"]*\{[^{}]*![rsa]\}""")
  ```
  关键问题：`[^'"]*` 假定 f-string 主体**不包含引号**。但 f-string 内只要含一个 `'`（如 `'1'`），正则就被切断。
- **复现**:
  ```python
  import re
  pat = re.compile(r"""f['"][^'"]*\{[^{}]*![rsa]\}""")
  line = 'f"stdout 应为 \'1\'，实际 {result[\'stdout\']!r}"'
  pat.search(line)  # → None（漏检！）
  ```
  对比：
  ```python
  line = 'f"value is {result["stdout"]!r}"'  # 双引号 dict 访问，能匹配
  pat.search(line)  # → 匹配
  ```
- **误报风险**: 反过来，正则只查 f-string 后跟 `{...!r}` 的情况，**不能**扫到非 f-string 字符串里的 `!r`（也不会有，所以没风险）。
- **影响范围**:
  - 当前仓库: 漏报 `test/test_tools.py:178`（P0 #1.1）
  - 未来: 任何在 f-string 主体内含 `'` 的 conversion 写法都会漏报
  - 这是一个"假绿"测试 —— 测试本身在跑、但它不真测试它声称测试的东西
- **建议**:
  1. **最简单**：把正则换成 `ast` 解析后走 `ast.FormattedValue` + `conversion` 字段（这是 3.6+ 都有的 AST 节点，能拿到真实的 conversion 数字 114/115/97）
  2. **次简单**：改进正则，处理 f-string 内的转义和引号：
     ```python
     pat = re.compile(r"""f['"](?:[^'"\\]|\\.)*\{(?:[^{}\\'"]|\\.)*![rsa]\}""")
     ```
  3. **加测试**：给 `test_py37_compat.py` 自己加一个 meta-test —— 往临时目录写一个含 `!r` 的文件，确认 `test_no_py38_fstring_conversion` 能 catch
- **严重程度理由**: 这是 dev_py37 的"质量门"——门本身坏了，意味着 dev 后续添加 3.8+ 语法不会被自动拦下来。

---

## 2. 一般问题（P1：影响可观测性、可维护性、可信度）

### 2.1 `verify_py37.sh` 在没有 3.7/3.8 时静默回退到 3.14，给出"全绿"假象

- **文件**: `scripts/verify_py37.sh:9-16`
- **现象**:
  - 当前环境是 3.14，`command -v python3.7` 找不到，`command -v python3.8` 找不到
  - 脚本**不报错**，只打 `[WARN] 没有 python3.7/3.8，回退到当前 python3（只做 AST + import 检查）`
  - 然后继续跑全套测试，最后 `echo "=== 全部通过 ==="`，退出码 0
- **问题**:
  1. **给 PM 错误的信号**：看上去在 3.7 下跑通了，实际只在 3.14 跑
  2. **无法被脚本自动检测**：`test/test_tools.py:178` 的 SyntaxError 在 3.14 下不会出现，所以 fallback 路径**完全不能**发现 P0 #1.1
  3. **CI 风险**：如果 CI 镜像只有 3.14，脚本永远"通过"，3.7 兼容性**事实上未验证**
- **建议**:
  1. fallback 路径必须 `exit 1`，并打明确错误：「没有 python3.7/3.8，请用 CI 镜像跑」
  2. 或者 fallback 只跑 AST 检查（test_py37_compat），但**禁止**跑 demo 和 test_xxx.py（因为 3.14 跑这些没有验证价值）
  3. 在 README/CI doc 里写明："本脚本必须配 3.7 或 3.8 才能给出有效信号"
- **严重程度理由**: 这个脚本是 dev_py37 分支对外承诺的"自动化验证"。如果它实际不起作用，3.7 兼容性等于口头承诺。

### 2.2 `test_py37_compat.py` 的 PEP 604 / PEP 585 正则误判率极高

- **文件**: `test/test_py37_compat.py:169-186, 205-229`
- **现象**:
  - `test_no_py39_pep585` 正则 `:\s*(list|dict|set|tuple|frozenset|...)\[` 只匹配 `:` 后直接跟 PEP 585 的形式
  - 漏掉 `Optional[list[int]]`（被 `Optional[...]` 包裹）
  - `test_no_py310_union_pipe` 正则要求 `|` 两侧都是**大写**类型名
  - 漏掉 `int | str`、`int | None`、`str | None` 等**最常见**的 PEP 604 写法
- **复现**:
  ```python
  import re
  pat = re.compile(r":\s*[\(]?\s*[A-Z][A-Za-z0-9_.\[\], ]*\|\s*[A-Z][A-Za-z0-9_.\[\], ]*[\)]?\s*(=|,|\)|\s*$)")
  pat.search("x: int | str = 0")  # → None（漏检）
  pat.search("def foo() -> int | str:")  # → None（漏检）
  pat.search("Optional[int | str]")  # → None（漏检）
  ```
- **实际影响**:
  - 当前仓库没有真用 PEP 604（小写 `|` 联合类型），所以测试**恰好**通过
  - 但**未来**任何人写 `x: int | None = None`（最常见写法）都不会被检测
  - 测试给的是"假绿"
- **建议**:
  1. 用 AST 检测：`ast.BinOp(op=ast.BitOr)` 在注解上下文（`Annotation`/`FunctionDef.returns`/`arg.annotation`）
  2. 或者改写正则：`r":\s*[\w\[\],. ]+\s*\|\s*[\w\[\],. ]+\s*[,=)\]]"`，并对大写/小写都允许
- **严重程度理由**: 这两条测试也属于"质量门"。门也是坏的。

### 2.3 实际改动行数与 PM 描述不符

- **PM commit message 说**: "agentflow/tools.py — 6 处、agentflow/graph.py — 2 处、test/test_tools.py — 5 处、test/test_planner.py — 1 处、test/test_debugger.py — 1 处" → 总计 **15 处**
- **实际 `git diff` 数**:
  - `agentflow/tools.py`: 6 行修改（每个一行 1 处 `!r`），符合
  - `agentflow/graph.py`: 2 行修改，符合
  - `test/test_tools.py`: 5 行修改（**漏 1 处**：line 178）—— 应当是 6 处
  - `test/test_planner.py`: 1 行修改，符合
  - `test/test_debugger.py`: 1 行修改，符合
  - **实际修改 = 15 处，少改了 1 处** = 16 处该改
- **严重程度理由**: PM 报告和 commit message 跟实际 diff 不一致，破坏可追溯性。Dev 漏改了 1 处就敢写"已通过 from __future__ 兼容 3.7"是过度自信。

---

## 3. 细节问题（P2：可读性、风格、小坑）

### 3.1 `verify_py37.sh` 用 `python3` 而非显式路径，PATH 依赖脆弱

- **文件**: `scripts/verify_py37.sh:9-16, 22-62`
- **现象**:
  - 脚本没有 shebang 指定 python 路径
  - fallback 路径用 `command -v python3` 拿 `PATH` 里的 `python3`
  - 在 PATH 异常的环境（如只有 Xcode 3.9 的 macOS 默认），`python3` 不是 homebrew 3.14，可能是 3.9
  - 实测：
    ```
    $ PATH=/usr/bin:/bin:/usr/local/bin command -v python3
    /usr/bin/python3    # 3.9.6
    $ PATH=/usr/bin:/bin:/usr/local/bin python3 -c "import ast; print(ast.Match)"
    AttributeError: module 'ast' has no attribute 'Match'
    ```
- **建议**:
  1. 脚本顶部加 `# shellcheck disable=...` 或者用 `which -a python3` 列出所有版本
  2. fallback 路径严格要求 `python3 --version` 输出必须是 3.7/3.8
  3. 或者直接 require 显式 `PY37=python3.7 ./scripts/verify_py37.sh`，不 fallback

### 3.2 `test_py37_compat.py` 顶层 `from __future__ import annotations` + 顶层 `_Py37CompatTests` 重复

- **文件**: `test/test_py37_compat.py:257-288`
- **现象**:
  - 顶层 10 个函数（`test_subprocess_new_api` 等） + 一个 `_Py37CompatTests(unittest.TestCase)` 包装类，类里又重新调这 10 个函数
  - 顶层函数和 `unittest` 类**重复定义**了同一组测试
  - 用 `python3 test/test_py37_compat.py` 跑顶层函数（用 10 PASS 输出）
  - 用 `python3 -m pytest test/test_py37_compat.py` 跑会收集到 20 个（10 个函数 + 10 个类方法）
- **影响**:
  - 测试计数虚高（看上去 20 通过，实际只测 10 个不重复的内容）
  - 维护时改一处忘改另一处的风险
- **建议**:
  1. 去掉 `_Py37CompatTests` 类，只保留顶层函数 + 末尾 `if __name__ == "__main__":` runner
  2. 或去掉顶层函数，只留 `unittest.TestCase` 方法
  3. 在 README/CI 文档里明确约定"本项目用 `python3 test/xxx.py` 直接跑，不用 pytest"（事实上 verify_py37.sh 也是这么做的）

### 3.3 `test_no_py310_union_pipe` 对 `dict | list` 这种 PEP 604 union 不报

- **文件**: `test/test_py37_compat.py:212-213`（正则）
- **细节**: 见 P1 #2.2
- **额外细节**: 即便考虑大写情形，正则要求结尾是 `=,)\s*$`，漏掉 `-> int | None:` 这种返回类型后面直接接 `:` 的

### 3.4 `test_no_py310_match_case` 在 3.9 下会 AttributeError 而非优雅失败

- **文件**: `test/test_py37_compat.py:189-202`
- **现象**:
  - 3.9 没有 `ast.Match` 节点
  - 如果在 3.9 跑这个测试，会抛 `AttributeError: module 'ast' has no attribute 'Match'`
  - 当前环境（3.14）有 `ast.Match`，所以测试通过
  - 在 fallback 路径下，如果 PATH 错位拉到了 Xcode 3.9，整个测试就 crash 了
- **建议**:
  ```python
  Match = getattr(ast, "Match", None)
  if Match is None:
      pytest.skip("Python < 3.10: ast.Match not available")
  ```
  或者在测试顶部 `sys.version_info >= (3, 10)` 时才检查 Match

### 3.5 `test_no_py38_fstring_debug` 和 `test_no_py38_fstring_conversion` 都跳过自己文件

- **文件**: `test/test_py37_compat.py:113-115, 132-135`
- **现象**: 两个测试都 `if path.endswith("test_py37_compat.py"): continue`
- **影响**: 文件自身 docstring 里的 `f'{var=}'`、`f'{var!r}'` 不会被检查（属于"自赦"）
- **建议**: 可以接受（docstring 本就不该被检查），但要在注释里说明白

### 3.6 `scripts/verify_py37.sh` 不跑 `test_py37_compat.py` 的 unittest 类

- **文件**: `scripts/verify_py37.sh:26`
- **现象**: 用 `python3 test/test_py37_compat.py` 直接跑，跑的是顶层 `if __name__ == "__main__"` 那个 runner，**不**走 unittest 路径
- **影响**: 跟 P2 #3.2 是一回事 —— pytest 看到 20 个，脚本看到 10 个
- **建议**: 统一成一种 runner（推荐直接用 `if __name__` runner + 顶层函数，最少 9 个文件都是这么做的）

### 3.7 PM commit message 错算"实际只需改 2 行"

- **PM 原话**: "实际只需改 f-string conversion 语法"
- **实际**: 17 处修改 + 1 个新测试文件 + 1 个新脚本
- **建议**: 后续 commit message 应由 `git diff --stat` 自动生成，而非手写

---

## 4. 设计层观察

### 4.1 AST 扫描的两种思路

- **当前做法**: 文本 regex + 手工忽略规则
  - 优点：实现简单
  - 缺点：edge case 多（已暴露 3 处 false negative/positive）
- **更稳做法**: 全 `ast.parse` 后走节点（`ast.FormattedValue.conversion`、`ast.Match`、`ast.BinOp` in annotation context、`ast.Subscript` value in `{list,dict,...}`）
  - 优点：精确，零误报
  - 缺点：实现稍复杂，要处理 `from __future__ import annotations` 下注解是字符串
- **建议**: 长期把 test_py37_compat.py 改成 AST 驱动。当前最小修复是修 P0 #1.2 的正则。

### 4.2 缺乏 3.7 真实跑测的环境

- 当前环境是 3.14 + 3.11
- 3.7/3.8 不可得
- 这意味着 dev_py37 分支**事实上未经验证**在 3.7 下能跑
- 建议 CI 加 Python 3.7 / 3.8 镜像任务（用 `python:3.7-slim` Docker image），强制 `verify_py37.sh` 在真 3.7 下通过

### 4.3 `from __future__ import annotations` 是这个分支的隐性基石

- 所有 8 个 agentflow 文件都有 `from __future__ import annotations`（**已检查**，全到位）
- 这让 `Optional[tuple[Any, str]]` 这种 3.9+ PEP 585 在 3.7 下能跑（注解是字符串，不 evaluate）
- 但未来如果有人加 `get_type_hints()` 调用（CLAUDE.md 提到的 llm.py 风险点），3.7 兼容会破
- 建议: 在 `agentflow/llm.py` 加注释"do not call get_type_hints"（CLAUDE.md 已经提了，但代码里没加防御）

### 4.4 f-string conversion → repr() 的输出等价性

- 抽查 4 处:
  - `f"{path!r}"` ≡ `f"{repr(path)}"`（在 3.8+ 都输出 `'value'`）
  - 在 3.7 下 `repr()` 行为完全一致（从 3.0 起稳定）
- 输出格式: 对字符串 `'foo'`，`!r` 和 `repr()` 都输出 `'foo'`（带单引号）—— 完全等价
- 对其他类型（数字、None、对象）也完全等价
- 结论: 业务行为无回归

---

## 5. 亮点

- ✅ `from __future__ import annotations` 在 8 个 agentflow 文件里全覆盖 —— 是这个分支能成功的关键
- ✅ `test_py37_compat.py` 自带 `if __name__ == "__main__"` 独立 runner，不依赖 pytest
- ✅ `verify_py37.sh` 用 `set -e` 早退 + 列出全部测试 —— 思路对，只是 fallback 路径设计有问题
- ✅ error message 转换正确（`apply_patch` 错误信息仍含 `'x.py'` 单引号）
- ✅ demo.py 7 个场景全过，业务逻辑无回归
- ✅ 没有引入新依赖（仍是零三方依赖）

---

## 6. 总评

- **P0**: 2 个（f-string conversion 漏改 #1 + 检测器假绿 #2）
- **P1**: 3 个（脚本 fallback 假信号 #3、PEP 604/585 正则烂 #4、PM 报告与实际不符 #5）
- **P2**: 6 个（PATH 依赖、测试重复、ast.Match 在 3.9 崩溃等）
- **整体评价**:
  - dev_py37 分支**目标对、方法对**，但**执行有重大遗漏**：核心改动漏 1 处（test_tools.py:178），且**质量门也是坏的**（test_py37_compat.py 的两个关键正则有 false negative）
  - 当前情况下：3.14 下能跑、3.7 下会 SyntaxError
  - PM 给的"只改 2 行"严重低估了工作量（实际 17 处+1 测试+1 脚本）；且 commit message 也漏算了 1 处
- **建议**: **有条件通过** —— 必须修复 P0 #1.1（漏改的 f-string conversion）和 P0 #1.2（检测器正则），重新跑全部测试 + 在 3.7/3.8 镜像上跑 `verify_py37.sh` 验证
- **不建议**: 现状下合入 master —— 即使 master 是 3.14-only，漏改的那行会在 3.7 环境被用户报错时定位不清
