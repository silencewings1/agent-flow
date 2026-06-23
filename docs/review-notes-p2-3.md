# CR 报告：P2-3 子图（feat/p2-3-subgraph）

> 审查分支：`feat/p2-3-subgraph`（commit `d675396`）
> 基准：`master`（`81fb8f8`）
> 审查日期：2026-06-23
> 审查人：CR Agent（独立窗口）
> Dev 自测声明：163 passed / 9 demo / py37 全过

---

## 审查结论：**PASS（可合并）**

核心设计正确，实现质量高，硬不变量（no-rerun）在子图场景下得到守护。
发现 0 个 P0、0 个 P1、2 个 P2 观察项、1 个文档不一致、1 个 master 既有 bug 暴露。

**PM 可在处理下述 P2/文档项后合并**（P2 不阻断合并，文档项建议 Dev 顺手修）。

---

## 审查范围

| 项 | 方法 |
|----|------|
| 代码 diff | `git diff master...HEAD`（5 文件，+853/-2） |
| 实现正确性 | 逐行审查 `_make_subgraph_fn` / `add_subgraph` / `to_mermaid`，追踪与 `_exec_node` / `invoke` / `_run_loop` 的交互 |
| 测试覆盖 | `test/test_subgraph.py` 8 个测试 + 边界遗漏分析 |
| 回归 | `pytest test/ -q`（163 passed）、`python -m demo`（9 场景）、`test_invariants.py`、`verify_py37.sh`（Python 3.7.17） |
| 对抗性 fuzz | 自建 `test/cr_fuzz_subgraph.py`（7 场景，21 断言） |

---

## 核心设计验证

### ✅ wrapper 语义正确

`_make_subgraph_fn` 把子图包成 NodeFn，复用 `_exec_node` 现有异常捕获：

- 子图 `interrupted` → `raise Interrupt(payload)` → 父图 `except Interrupt` 捕获 ✓
- 子图 `failed` → `raise RuntimeError` → 父图 `except Exception` 捕获（走节点级重试）✓
- 子图 `completed` → `output_map` 映射回父 partial update ✓

**关键：主循环 `_run_loop` 一行未改**，完全通过现有机制实现冒泡。这是本设计最大的优点——零侵入。

### ✅ thread_id 稳定性（resume 守护）

sub_tid = `{parent_tid}::sub::{name}::s{ctx.step}::{ctx.instance_id}`

追踪 resume 路径确认：
- 中断时 checkpoint 存 `step - 1`（`graph.py:993`）
- 恢复时 `step = prev.step`（`graph.py:874`）
- `_run_loop` 重新 `step += 1`（`graph.py:910`）回到中断时的值
- **所以 `ctx.step` 在首次执行和 resume 时一致**，sub_tid 稳定 ✓

测试 6（`test_subgraph_no_rerun_on_parent_resume`）+ Fuzz 3 + Fuzz 6 三重守护此不变量。

### ✅ resume 透传

`ctx.resume_value` → `Command(resume=...)` → `sub.invoke(command=...)` 链路正确。
Fuzz 3 验证 resume 后子图 activity cache 命中（工具不重跑）。

### ✅ state 隔离

`input_map` / `output_map` 双向控制数据流，未声明字段不泄漏。
测试 4 + Fuzz 5 双重验证。

### ✅ 嵌套支持

Fuzz 1 验证 3 层嵌套端到端完成，结果逐层冒泡正确。

---

## 对抗性 Fuzz 结果

`test/cr_fuzz_subgraph.py`（7 场景，21 断言）：

| Fuzz | 场景 | 结果 |
|------|------|------|
| 1 | 嵌套 3 层子图 | ✅ 4/4 |
| 2 | 子图内 Send 动态扇出 | ✅ 2/2 |
| 3 | resume 后子图 activity cache 命中 | ✅ 4/4 |
| 4 | 父图回环导致子图多次重入 | ✅ 2/2 |
| 5 | output_map 指向不存在的 child key | ✅ 3/3 |
| 6 | 子图中断 + 父图兄弟节点已完成 | ✅ 6/6 |
| 7 | 空子图（START → END 无业务节点） | ❌ 1/1 失败 |

**Fuzz 7 失败分析**：见下方"发现"§3。**属 master 既有 bug，非 P2-3 引入**。

---

## 发现

### P0（阻断合并）：无

### P1（合并前必修）：无

### P2（观察项，不阻断）

#### P2-1：子图 failed 后父图节点级重试无效

**现象**：子图因 max_steps 死循环 failed 后，父图若配置了 `retries > 0`，重试时子图从 failed checkpoint 恢复，**再次立即 failed**，重试次数耗尽后父图 failed。

**根因**：`_exec_node` 重试时 `ctx.step` 和 `ctx.instance_id` 不变（`graph.py:1048-1049`），所以子图 wrapper 用相同的 sub_tid 调 `sub.invoke`，走恢复路径读到 failed checkpoint，死循环 frontier 再次触发 max_steps。

**影响**：低。子图 failed 通常是逻辑性错误（死循环），简单重试本就不会解决。父图重试更适合"瞬时错误"（LLM API 超时），那种情况下子图无 failed checkpoint，会从头跑。

**建议**：文档化此行为——"子图 failed 后父图重试不会清空子图状态；如需子图重试时从头跑，需调用方在外层用不同 thread_id"。不强制改代码。

**复现**：
```python
sub = StateGraph(max_steps=2)
sub.add_node('loop', lambda s,c: {})
sub.add_edge(START, 'loop')
sub.add_conditional_edges('loop', lambda s: 'loop')
main = StateGraph()
main.add_subgraph('looper', sub.compile(Checkpointer()))
# 手动给 looper 节点加 retries=1
main._nodes['looper'].retries = 1
res = main.compile(Checkpointer()).invoke({}, thread_id='t')
# 期望：重试后仍 failed（符合预期），但 error 显示重试发生过
```

#### P2-2：`add_subgraph` 不支持 `retries` / `retry_backoff` 参数

**现象**：`add_node` 支持 `retries` / `retry_backoff`，但 `add_subgraph` 不支持。用户无法给子图节点配置重试。

**影响**：低。当前无使用场景要求子图重试（见 P2-1，重试对 failed 子图无效）。

**建议**：后续如需，给 `add_subgraph` 加 `retries` / `retry_backoff` 参数透传给 `_Node`。本轮不修。

### 文档不一致

#### 文档-1：plan §1.2 API 签名与 §4.4 / 实现不一致

**现象**：`docs/plan-p2-3-subgraph.md` §1.2 的 API 签名展示：
```python
add_subgraph(name, subgraph, input_map, output_map,
             max_steps: Optional[int] = None)  # None=继承父 max_steps
```
但 §4.4 明确说"本计划采用后者（不实现 max_steps 参数）"，实际实现也没有 `max_steps` 参数。

**影响**：读者看 §1.2 会误以为有 `max_steps` 参数。

**建议**：Dev 顺手修 §1.2 的签名，删掉 `max_steps` 参数行，与 §4.4 和实现对齐。~2 行改动。

### 既有 bug 暴露（非 P2-3 引入）

#### 既有-1：空图 `START → END` 触发 `KeyError: '__end__'`

**现象**：`StateGraph` 只 `add_edge(START, END)` 不加任何业务节点时，`invoke` 报 `KeyError: '__end__'`。

**根因**：`_entry` 包含 `END`（`graph.py:283`），`_run_loop` 把 `END` 当普通节点 `self.g._nodes[item["node"]]`（`graph.py:925`）触发 KeyError。

**影响**：P2-3 之前极少触发（没人写空图）；P2-3 后"空子图"成为更易触达的场景（用户可能写 `sub.add_edge(START, END)` 作为占位）。

**验证**：在 master 上复现，确认是既有 bug。
```bash
git checkout master -- agentflow/graph.py
python -c "from agentflow import *; StateGraph().add_edge(START, END).compile(Checkpointer()).invoke({}, thread_id='t')"
# → KeyError: '__end__'
```

**建议**：独立修复（`_run_loop` 入口过滤 `item["node"] == END` 即可），不阻塞 P2-3 合并。归入 CR backlog。

---

## 测试覆盖评估

| plan §5 要求 | 实现状态 | 评估 |
|--------------|----------|------|
| 1. 子图运行 + output_map | ✅ `test_subgraph_runs_and_returns_output` | 充分 |
| 2. 中断冒泡 + resume | ✅ `test_subgraph_interrupt_bubbles_to_parent` | 充分 |
| 3. max_steps 超限 | ✅ `test_subgraph_max_steps_failure_bubbles` | 充分 |
| 4. state 隔离 | ✅ `test_subgraph_state_isolation` | 充分 |
| 5. 嵌套 2 层 | ✅ `test_nested_two_level_subgraph` | 充分（Fuzz 1 补充 3 层） |
| 6. no-rerun 子图版 | ✅ `test_subgraph_no_rerun_on_parent_resume` | 充分 |
| 附加：to_mermaid | ✅ `test_to_mermaid_renders_subgraph_label` | 充分 |
| 附加：类型校验 | ✅ `test_add_subgraph_rejects_non_compiled_graph` | 充分 |

**覆盖度评估**：高于 plan 要求。Fuzz 额外覆盖了子图内 Send、回环重入、兄弟节点 no-rerun、activity cache 命中等场景。

**未覆盖但可接受**：
- 子图内条件边返回 END（Fuzz 中手动验证过，行为正确）
- 子图 + 父图条件边回环组合（Fuzz 4 覆盖）
- 嵌套 4 层以上（3 层已证明递归机制正确，更深层数无新增风险）

---

## 回归验证

| 检查 | Dev 声明 | CR 复现 | 一致 |
|------|----------|---------|------|
| `pytest test/ -q` | 163 passed | 163 passed | ✅ |
| `python -m demo` | 9 场景全过 | 9 场景全过 | ✅ |
| `test_invariants.py` | 通过 | 通过 | ✅ |
| `verify_py37.sh` (3.7.17) | 12 项全过 | 12 项全过 | ✅ |

---

## 代码质量

| 维度 | 评估 |
|------|------|
| 可读性 | ✅ wrapper 逻辑清晰，注释充分，5 步编号对应 docstring |
| 与现有架构一致 | ✅ 复用 `_Node` / `NodeFn` / `Interrupt` / `Command`，零新基础设施 |
| 错误处理 | ✅ 类型校验（`isinstance`）、保留节点名校验、output_map 缺失 key 跳过 |
| Python 3.7 兼容 | ✅ 无 PEP 604/585 语法，dataclass / f-string / typing 均已用 |
| 测试规范 | ✅ 仿 `test_send.py` 风格，`ALL_TESTS` + `__main__` 入口 |

---

## 建议的合并前动作（Dev 顺手修，非阻断）

1. **文档-1**：修 `docs/plan-p2-3-subgraph.md` §1.2 的 API 签名，删掉 `max_steps` 参数行（~2 行）
2. **可选**：把 `test/cr_fuzz_subgraph.py` 纳入版本控制（CR 证据，函数名无 `test_` 前缀不污染 pytest）

---

## 总结

P2-3 子图实现**设计优雅、质量过硬**：通过把子图包成 NodeFn 完全复用现有执行/中断/checkpoint 机制，主循环零改动，硬不变量在子图场景下得到守护。8 个单元测试 + 7 个对抗性 fuzz 覆盖了核心场景和边界。

发现的 2 个 P2 观察项（failed 后重试无效、不支持 retries 参数）均属低影响设计张力，不阻断合并。1 个文档不一致建议 Dev 顺手修。1 个既有 bug（空图 KeyError）归入 backlog。

**CR 通过，建议 PM 合并。**
