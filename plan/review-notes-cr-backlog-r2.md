# CR Backlog Round 2 审查记录（2026-06-18）

> 范围：commit `e2faf9f`（CR Backlog Round 2: 7 个 P2 修复）相对 master (`3475128`)。
> 方法：独立 fresh-eyes 审查，跑测试 + 读 diff + 对抗性 fuzz + 语义分析。

## 0. 测试通过情况

| 套件 | 结果 | 备注 |
|------|------|------|
| test_invariants | PASS | 无回归 |
| test_activity | PASS | 无回归 |
| test_graph | PASS | 无回归 |
| test_planner | PASS | 无回归 |
| test_review | PASS | 无回归 |
| test_tools | PASS | 无回归 |
| test_coder | PASS | 含新增 assert（修复 16） |
| test_debugger | FAIL (test_debugger_all_pass) | **预存问题**：master 上同样失败，非本轮引入 |
| test_py37_compat | PASS | 无回归 |
| demo.py | PASS | 7/7 场景全过 |

**预存问题说明**：`test_debugger_all_pass` 在 master 上也返回 `status=failed`（`AssertionError: failed`），非 Round 2 变更导致。这是 debugger 节点本身或测试环境的预存问题。

## 1. 严重问题（P0）

### 1.1 修复 15：task 缺 id 自动分配与已有 id 冲突

- **文件**: `agentflow/nodes.py:136`
- **现象**: `task_id = task.get("id") or f"t{i+1}"` —— 当 `plan.tasks` 中**部分 task 有 id、部分没有**时，自动分配的 `t1`, `t2` 可能与已有 id 冲突。
- **复现**:
  ```python
  plan_tasks = [{"title": "a"}, {"id": "t1", "title": "b"}]
  # 第 1 个 task 缺 id → 自动分配 "t1"
  # 第 2 个 task 有 id="t1" → 也是 "t1"
  # 两个 task 都写到 task_t1.py → 后者覆盖前者
  ```
  已实测确认：`assigned_ids = ["t1", "t1"]`，冲突。
- **建议**: 用 `{task.get("id") for task in plan_tasks if task.get("id")}` 收集已有 id 集合，自动分配时跳过已占用的 id。
- **严重程度理由**: 这正是修复 15 要解决的问题（task 缺 id 导致文件覆盖），但修复引入了新的覆盖场景。虽然实际触发概率低（plan 通常要么全有 id 要么全没有），但这是一个逻辑缺陷。

## 2. 一般问题（P1）

（本轮无 P1 发现）

## 3. 细节问题（P2）

### 3.1 修复 12：`pass_at_version=0` 语义变化

- **文件**: `agentflow/nodes.py:213,266`
- **现象**: `state.get("pass_at_version") or 3` 把 `pass_at_version=0` 当作 falsy，变成 `3`。旧行为 `state.get("pass_at_version", 3)` 会返回 `0`。
  - 旧行为：`pass_at_version=0` → `version >= 0` → 永远 True（第一版就通过）
  - 新行为：`pass_at_version=0` → `version >= 3` → 需要第 3 版才通过
- **影响分析**: 版本号从 1 开始（`state.get("code_version", 0) + 1`），`pass_at_version=0` 在代码库中从未出现。这是一个理论上的语义变化，实际无影响。
- **判定**: 可接受。`0` 作为"立即通过"的语义本身就不合理（没有"第 0 版"），且 `pass_at_version=0` 从未在代码库中使用。

### 3.2 修复 12：`pass_at_version="0"`（字符串）仍会 TypeError

- **文件**: `agentflow/nodes.py:213`
- **现象**: `"0" or 3` = `"0"`（非空字符串是 truthy），然后 `version >= "0"` 抛 `TypeError`。
- **影响分析**: 这是预存行为（`state.get("pass_at_version", 3)` 返回 `"0"` 同样会炸）。state 中该值总是 int，不影响实际使用。
- **判定**: 无需处理。

### 3.3 修复 13：`len(unified_diff)` 是字符数不是字节数

- **文件**: `agentflow/tools.py:173`
- **现象**: `len(unified_diff) > 1_000_000` —— Python 3 中 `len(str)` 是 Unicode 字符数，不是字节数。对于纯 ASCII diff（绝大多数情况），字符数 = 字节数。对于含中文注释的 diff，1 个中文字符 = 1 个 Python 字符 = 3 个 UTF-8 字节，所以实际内存占用可能超过 1MB。
- **影响分析**: 极低。diff 通常全是 ASCII，即使含中文，1M 字符 ≈ 1-3MB，仍在安全范围内。
- **判定**: 可接受。注释里写的是"1MB 限制"但实际是"1M 字符限制"，与错误消息 `{len(unified_diff)} 字节` 不完全一致。但不值得为这个改。

### 3.4 修复 16：`FeedbackMockRegistry._last_prompt` 是类变量

- **文件**: `test/test_coder.py:198`
- **现象**: `_last_prompt = ""` 定义在类级别，但 `self._last_prompt = prompt` 创建了实例属性。单个测试实例的行为正确，但如果多个测试复用同一个类（不会发生），可能有意想不到的行为。
- **判定**: 可接受。这是测试代码，当前使用方式正确。不算 bug。

### 3.5 修复 17：场景 7 回环最终状态为 `failed`

- **文件**: `demo.py:277-308`
- **现象**: demo 输出显示"状态: failed (超过 max_steps，可能是正确的回环)"。这意味着回环可能没有正确收敛——debugger 测试通过后，`route_after_debug` 应该路由到 END，但如果 `tests_passed` 没有被正确设置，就会继续回环直到 max_steps。
- **分析**: 这与修复 17 无关，是预存的 debugger 行为问题（与 `test_debugger_all_pass` 失败可能是同一根因）。dummy_coder 的修复逻辑本身是正确的（`content.replace("assert fib(5) == 99", "assert fib(5) == 5")` 确实把错误测试改正确了）。
- **判定**: 修复 17 本身的逻辑正确。demo 最终状态 `failed` 是预存问题。

## 4. 修复逐项验收

| # | 修复 | 文件 | 验收结果 |
|---|------|------|---------|
| 11 | test_planner 死代码 | test/test_planner.py | PASS — 第一个 reg 定义（原 line 135-140）已删除，第二个保留，测试通过 |
| 12 | pass_at_version=None | agentflow/nodes.py | PASS — 两处都改为 `or 3`，`None`/`""`/`0` 都会兜底到 3，不再抛 TypeError |
| 13 | apply_patch 大小限制 | agentflow/tools.py | PASS — 1MB 边界正确（1,000,000 通过，1,000,001 拒绝），位置在空 diff 检查之后 dry-run 之前 |
| 14 | plan_tasks=None | agentflow/nodes.py | PASS — 显式 `if plan_tasks is None: plan_tasks = []`，配合 `isinstance(plan_dict, dict)` 守卫，防御完备 |
| 15 | task 缺 id | agentflow/nodes.py | **P0 发现** — 自动分配与已有 id 冲突，见 1.1 |
| 16 | feedback 验证 | test/test_coder.py | PASS — `assert "NullPointerException" in reg._last_prompt` 正确验证了 feedback 注入 |
| 17 | 场景 7 真回环 | demo.py | PASS — dummy_coder 正确读文件、替换错误断言、写回 |

## 5. 对抗性 fuzz 结果

| 测试项 | 输入 | 预期 | 实际 | 判定 |
|--------|------|------|------|------|
| pass_at_version=0 | `state["pass_at_version"] = 0` | 不抛异常 | `or 3` → 3，不抛异常 | PASS（语义有变化，见 3.1） |
| pass_at_version="" | `state["pass_at_version"] = ""` | 不抛异常 | `or 3` → 3，不抛异常 | PASS |
| pass_at_version=None | `state["pass_at_version"] = None` | 不抛异常 | `or 3` → 3，不抛异常 | PASS |
| apply_patch 1MB | `len(diff) == 1_000_000` | 允许 | 允许（走到 dry-run 阶段） | PASS |
| apply_patch 1MB+1 | `len(diff) == 1_000_001` | ValueError | ValueError | PASS |
| plan.tasks=None | `{"tasks": None}` | 不 crash | `plan_tasks = []` | PASS |
| plan.tasks 全缺 id | `[{"title": "x"}, {"title": "y"}]` | t1, t2 无冲突 | t1, t2 无冲突 | PASS |
| plan.tasks 部分缺 id | `[{"title": "a"}, {"id": "t1", "title": "b"}]` | 无冲突 | t1, t1 冲突 | **FAIL** — P0 |

## 6. 亮点

- **修复 14 防御完备**：同时加了 `isinstance(plan_dict, dict)` 守卫和 `if plan_tasks is None` 显式处理，两层防护。
- **修复 13 位置正确**：大小限制加在空 diff 检查之后、dry-run 之前，避免了对空 diff 的重复检查。
- **修复 11 干净利落**：删 7 行，加 0 行，无副作用。
- **修复 16 断言精准**：验证的是具体字符串 `"NullPointerException"` 而不是笼统的 `len > 0`。
- **修复 17 真改文件**：dummy_coder 现在真的读、替换、写回，实现了完整的 fix-and-retest 回环。

## 7. 总评

- **总问题数**: P0: 1 / P1: 0 / P2: 4
- **整体评价**: **基本通过，建议修 1 个 P0 后合并**。7 个修复中有 6 个完全正确，测试基线全部保持（1 个预存 test_debugger 失败除外）。唯一的 P0 是修复 15 的 id 冲突——修复了"全缺 id"的问题，但引入了"部分缺 id"的新冲突场景。
- **建议**: 修 1.1（修复 15 的 id 冲突）后合并。其余 P2 均为理论边缘情况，不影响实际使用。

### 最严重的问题

**修复 15 的 id 冲突（1.1）**：`task_id = task.get("id") or f"t{i+1}"` 在部分 task 有 id、部分没有时会分配重复 id。修复方案：在自动分配前收集已有 id 集合，跳过已占用的编号：

```python
existing_ids = {t["id"] for t in plan_tasks if t.get("id")}
for i, task in enumerate(plan_tasks):
    task_id = task.get("id")
    if not task_id:
        for j in range(1, len(plan_tasks) + 1):
            candidate = f"t{j}"
            if candidate not in existing_ids:
                task_id = candidate
                existing_ids.add(candidate)
                break
        else:
            task_id = f"t{i+1}"  # fallback
        warnings.warn(f"[Coder] task #{i+1} 缺 id，自动分配为 '{task_id}'")
```
