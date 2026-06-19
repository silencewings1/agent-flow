# 审查记录 — 2026-06-17

## feat/p0-graph-validate（commit 3d703e4）

### 审查范围

`agentflow/graph.py` +244 行，`test/test_graph.py` 新增 345 行。核心功能：`StateGraph.validate()` 静态校验 + `to_mermaid()` 导出。

### 测试结果

- `test/test_graph.py` — ✅ 15/15 通过
- `test/test_invariants.py` — ✅ 无回归
- `python3 demo.py` — ✅ 5 个场景正常

### 问题清单

#### 问题 1：AST 嵌套函数字符串误提取（已修复 ✅）

上一轮审查指出的问题，当前代码已用 `_SKIP_SUBTREE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)` 手动遍历 AST，跳过嵌套函数/ lambda 内部，不再误提取。

#### 问题 2：BFS 用 `list.pop(0)` 性能问题（已修复 ✅）

当前代码已改用 `collections.deque` + `popleft()`，O(1) 出队。

#### 问题 3：test_dead_end_node_is_warning 名不副实（低风险，沿用）

测试名叫 "is_warning"，但注释承认应该给 info，断言也只检查了「不是 error」。实际运行时 `dead` 节点无出边，被反向 BFS 当作合法终端（`not outs` → `reaches_end`），所以不会产生任何 issue。建议改名为 `test_node_with_no_edges_is_valid_terminal`。

---

### 本轮新发现

#### 问题 4（minor）：validate() 不检查 entry 节点是否已定义

**位置**：`agentflow/graph.py:150-152`

`add_edge(START, "ghost")` 当 `"ghost"` 未通过 `add_node` 注册时，`validate()` 不会报 error（BFS 会跳过它，因为 `n not in self._nodes`），但 `compile()` 会抛 `ValueError`。应在 `validate()` 中也显式检查，保持一致性。

**建议修复**：在检查 1（入口存在）之后增加：

```python
for n in self._entry:
    if n not in self._nodes:
        issues.append(ValidationIssue(
            "error", f"入口引用了未定义节点: {n}"))
```

#### 问题 5（style）：变量命名误导

**位置**：`agentflow/graph.py:230`

```python
bad = self._static_string_returns(router)
```

`bad` 包含所有字符串字面量（合法和非法），并非仅 "bad"。建议改为 `targets` 或 `candidates`。

#### 问题 6（minor）：to_mermaid 条件边使用 `????` 占位符

**位置**：`agentflow/graph.py:357`

`????` 会被 Mermaid 渲染为一个独立节点而非表示「目标未知」。建议改用更清晰的占位符。

#### 问题 7（coverage）：缺少 entry 指向未定义节点的测试

没有覆盖 `add_edge(START, "nonexistent")` 的场景，对应问题 4。

---

### 亮点

- **检查覆盖完整**：8 种检查（入口、可调用性、未定义节点、可达性、死胡同、重复边、条件边目标、循环），每种都有对应测试
- **error/warning/info 三级分层合理**：条件边相关给 warning 不阻塞编译，循环给 info 仅提示
- **`_static_string_returns` AST 启发式设计精巧**：处理了三元、and/or、列表/元组、Dict、Call 等多种组合，且正确跳过嵌套函数
- **`to_mermaid()` 实用**：条件边用虚线 + 函数名标注，一目了然
- **API 风格一致**：`validate()` 返回 list 让调用方自行决定处理方式

### 总结

**代码质量高，逻辑正确，无关键 bug。** 上一轮的 3 个问题已修 2 个，本轮新发现 4 个都是 minor/coverage 级别，不阻塞合并。建议修复问题 4（entry 未定义节点检查）和问题 5（变量命名），其他可选。

---

## 第二轮审查（2026-06-17，commit cc11f93）

### 审查范围

第二轮审查 `feat/p0-graph-validate` 分支的 `cc11f93` 提交，验证上一轮指出的 3 个问题的修复情况，并做最终确认。

### 代码改动（相对 3d703e4）

- `agentflow/graph.py` — 39 行修改：AST 嵌套过滤、BFS deque 优化
- `test/test_graph.py` — 48 行修改：新增 `test_conditional_returns_nested_function_not_extracted` 测试

### 测试结果

| 测试套件 | 结果 |
|---|---|
| `PYTHONPATH=. python3 test/test_graph.py` | **16/16 通过**（新增 1 个：`test_conditional_returns_nested_function_not_extracted`） |
| `PYTHONPATH=. python3 test/test_invariants.py` | **2/2 通过，无回归** |
| `python3 demo.py` | **5/5 场景正常** |

### 修复确认

#### 问题 1：AST 嵌套函数字符串误提取 — **已修复 ✅**

`_static_string_returns()` 现已使用手动 AST 遍历 + `_SKIP_SUBTREE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)` 跳过嵌套函数/lambda 内部。`test_conditional_returns_nested_function_not_extracted` 覆盖此场景，通过。

#### 问题 2：BFS 用 `list.pop(0)` 性能问题 — **已修复 ✅**

BFS 正向传播（第 4 项检查）和反向传播（第 5 项检查）均已改用 `collections.deque` + `popleft()`。

#### 问题 3：test_dead_end_node_is_warning 名不副实 — **已修复 ✅**

已更名为 `test_node_with_no_edges_is_valid_terminal`，断言和注释同步更新，反映「无出边 = 合法终端」的实际语义。

### 代码质量评估

**`validate()` BFS 可达性逻辑：正确 ✅**

- 正向 BFS：从 `_entry` 出发遍历静态边 + 条件边（AST 提取的目标），正确跳过 `END` 和 `not in self._nodes` 的节点
- 反向 BFS：从「连到 END / 无出边 / 有条件边」的节点反向传播，正确识别无路径到 END 的节点
- 条件边 AST 拿不到目标时退到「所有节点」做最保守估计，不会漏报

**`_static_string_returns()` AST 静态分析：合理 ✅**

- 正确处理：`return "x"`、三元 `if/else`、`and/or`、列表/元组/Dict 字面量
- 正确跳过：嵌套函数/lambda 子树、函数调用返回值
- 边界情况有 fallback：`inspect.getsource` 失败退到空集，不会 crash
- 局限（已知且可接受）：无法处理变量引用、条件分支中的复杂表达式，但这属于 warning 级别，噪声可控

**`to_mermaid()` 输出格式：正确 ✅**

- 生成标准 `graph TD` 格式，含 `:::startNode` / `:::endNode` CSS class
- 静态边用 `-->` 实线，条件边用 `-.->|label|` 虚线 + 函数名标注
- 节点名做基础转义（空格/连字符/点替换为下划线）
- 条件边的 `????` 占位符（问题 6）在 Mermaid 中会渲染为独立节点，但不影响图结构

**测试覆盖：充分 ✅**

16 个测试覆盖了所有 8 项 validate 检查的正反场景，以及 to_mermaid 的基本输出。唯一缺失的是 `add_edge(START, "nonexistent")` 的测试（问题 7），对应 validate 中未检查 entry 目标节点是否定义的问题（问题 4）。

### 遗留问题（均不阻塞合并）

#### 问题 4（minor）：validate() 不检查 entry 节点是否已定义

`add_edge(START, "ghost")` 当 `"ghost"` 未注册时，`validate()` 不报错（BFS 因 `n not in self._nodes` 跳过），但 `compile()` 会抛 `ValueError`。应在 validate 中增加显式检查。

#### 问题 5（style）：变量命名 `bad` 误导

`agentflow/graph.py:230` 的 `bad` 变量实际包含所有字符串字面量（含合法节点），建议改名为 `targets`。

#### 问题 6（minor）：to_mermaid 条件边 `????` 占位符

Mermaid 会将 `????` 渲染为独立节点。建议改为 `???` 或 `(unknown)` 等不渲染为节点的占位符。

#### 问题 7（coverage）：缺少 entry 指向未定义节点的测试

对应问题 4，建议新增测试用例覆盖。

### 最终结论

**审查通过。** 代码质量高，逻辑正确，所有测试通过。上一轮审查指出的 3 个问题均已修复，本轮发现的 4 个问题均为 minor/coverage 级别，不阻塞合并。推荐合入 master。

---

## P0-1 审查 — 2026-06-17

### 审查范围

`feat/p0-1-activity-cache` 分支（commit `5cb76d3`），实现 `ctx.activity(key, fn)` 缓存机制。改动涉及 `agentflow/checkpoint.py` (+38 行), `agentflow/graph.py` (+15 行), `agentflow/nodes.py` (-10/+10 行), `test/test_activity.py` (+235 行)。

### 测试结果

| 测试套件 | 结果 |
|---|---|
| `test/test_activity.py` | **7/7 通过**（首次执行、缓存命中、不同 key、不同 thread、异常缓存、复杂类型、无 checkpointer 退化） |
| `test/test_invariants.py` | **2/2 通过**（无回归） |
| `python3 demo.py` | **5/5 场景正常**（场景 1 的中断/恢复流程验证了 activity 机制） |

### CR 重点审查点分析

#### 1. 缓存键 `(thread_id, node, activity_key)` — 足够唯一 ✅

**结论：通过。** 三个维度覆盖了所有可能的冲突场景：
- `thread_id`：不同执行实例隔离（`test_different_threads_independent` 已验证）
- `node`：同一 thread 内不同节点即使使用相同 activity_key 也不会冲突
- `activity_key`：同一节点内多次 LLM 调用（如 reviewer 只有一次，但理论上可扩展）各自独立

无遗漏维度。不需要 `step` 维度，因为 checkpoint 恢复时同一节点不会重跑（frontier 保证），即使同一节点在同一 thread 内被多次调用（如回环中 coder 反复执行），每次调用是**不同节点实例**（同一节点名），activity 按 `(thread_id, node, key)` 缓存意味着：
- 首次 coder 执行：`(tid, "coder", "llm_complete")` → 写入缓存
- 回环后 coder 再次执行：`(tid, "coder", "llm_complete")` → 命中缓存，不会重新调用 LLM

**⚠️ 但这是否正确？** 回环场景下，Coder 每次重入时 state 不同（`code_version` 递增、`test_failures` 变化），如果命中缓存会返回**第一次**的结果——而第一次的 prompt 包含了旧的 `test_failures`，第二次需要重新生成修复代码。这在 demo 的回环场景（scenario 1）中可能有问题。

**验证**：重新审视 demo 场景 1 的执行轨迹，观察到 Coder 执行了 4 次（v1, v2, v3, v4），且 debugger 返回了不同的 test_failures。这说明 activity 缓存**不会跨 super-step 生效**——每次节点被调度进 frontier 是独立的 `_exec_node` 调用，但 `_exec_node` 内部 activity 调用依然会命中上一次执行写入的缓存。

**实际排查**：看 `_exec_node` 和 `_run_loop` 的逻辑——当 coder 在 super-step N 被调度，`_exec_node` 被调用，`ctx.activity("llm_complete", ...)` 会去查 `activity_results` 表。如果表中已有 `(thread_id, "coder", "llm_complete")`，就会**命中缓存**返回旧结果。这意味着 demo 场景 1 中 Coder 的 4 次执行实际上每次返回的是相同的 LLM 结果——但由于 mock LLM 的返回不依赖 prompt（始终返回 `[mock:...] 针对「...」的生成结果`），所以 demo 看起来正常。但接入真实 LLM 后，这会导致**回环中 Coder 的 LLM 调用实际上只执行了一次，后续都命中缓存**。

**严重程度：中等。** 需要修复。

#### 2. 异常处理 — 正确 ✅

**结论：通过。** `activity()` 方法中：
- `fn()` 抛出异常时：`str(exc)` 序列化写入 `activity_results` 表，`status="exception"` ✅
- 重入时：从缓存读取 `status="exception"`，用 `RuntimeError(str(result))` 重抛 ✅
- `fn()` 不会再次执行 ✅
- 测试 `test_exception_is_cached` 已验证 ✅

边界情况考虑：`str(exc)` 只保留异常消息，丢失了 traceback 和异常类型信息。这在大多数场景下够用（节点代码通常只 catch Exception 看 message），但对于需要精确异常类型的场景（如区分 `ValueError` vs `KeyError`）会丢失信息。建议改为 `json.dumps({"type": type(exc).__name__, "message": str(exc)})`，但当前实现不影响功能正确性，属于增强建议。

#### 3. 并发安全 — 正确 ✅

**结论：通过。** `put_activity` 和 `get_activity` 都使用了 `self._lock`（同一个 `threading.Lock`），与 `put()` 和 `log_event()` 共享同一把锁。SQLite 连接是同一个，`check_same_thread=False`。线程池内多节点并发写入时，锁保证串行化。✅

潜在问题：`get_activity` 读取时不持有锁。理论上存在极小窗口：两个线程同时 get 同一个 key 都返回 None，然后都执行 fn()，然后都 put。但 `INSERT OR REPLACE` 保证最终只有一条记录，且写入时间可能不同但结果相同（因为 fn 的输入相同）。对于幂等的 LLM 调用，多执行一次的影响仅限于 token 浪费，不会导致状态不一致——这是**可接受的折中**。如需严格防重，可改用 `INSERT ... WHERE NOT EXISTS` + 事务重试，但当前方案性价比更高。

#### 4. 序列化边界 — 存在已知局限 ✅

**结论：通过。** `json.dumps` / `json.loads` 的序列化边界：
- **支持**：`str`, `int`, `float`, `bool`, `None`, `list`, `dict`（含嵌套），以及这些类型的组合 ✅
- **不支持**：自定义对象、`datetime`、`set`、`tuple`（会变成 list）、`bytes`、函数、生成器等

当前使用场景中，LLM 调用 `get_registry().complete()` 返回 `str`，所以完全没问题 ✅。`nodes.py` 中所有节点都用 `ctx.activity("llm_complete", lambda: ...)` 包裹 LLM 调用，返回值是字符串，序列化无风险。

如果将来 `activity()` 被用于缓存其他类型（如中间计算结果），需要调用方确保返回值可被 JSON 序列化。当前 API 设计没有强制约束，`fn()` 返回不可序列化对象时 `put_activity` 会抛出 `TypeError`。

#### 5. thread_id 和 checkpointer 注入方式 — 合理 ✅

**结论：通过。** `_exec_node` 中构造 `NodeContext` 时直接注入 `thread_id` 和 `self.cp`：

```python
ctx = NodeContext(node.name, step, attempt, resume_value, thread_id, self.cp)
```

`NodeContext` 的 `thread_id` 默认 `""`，`_cp` 默认 `None`，当 `_cp` 为 None 时 `activity()` 退化为直接调用 `fn()`（`test_activity_without_checkpointer` 已验证）✅。

这是最直接的注入方式，无过度工程。没有通过 contextvars 或全局变量传递，避免了隐式依赖。

#### 6. NodeContext.activity() 边界检查 — 一个边缘问题

**结论：基本通过，有一个边缘问题。**

```python
def activity(self, key: str, fn: Callable[[], Any]) -> Any:
    if self._cp is None:
        return fn()
    cached = self._cp.get_activity(self.thread_id, self.node, key)
    if cached is not None:
        result, status = cached
        if status == "exception":
            raise RuntimeError(str(result))
        return result
    try:
        result = fn()
        self._cp.put_activity(self.thread_id, self.node, key, result, "success")
        return result
    except Exception as exc:
        self._cp.put_activity(self.thread_id, self.node, key, str(exc), "exception")
        raise
```

- **退化逻辑**：`_cp is None` 时直接调用 `fn()`，不缓存 ✅
- **正常路径**：先查缓存 → 命中返回 → 未命中执行 fn → 写入缓存 ✅
- **异常路径**：写入异常缓存 → 重抛异常 ✅
- **重入异常路径**：读取异常缓存 → `RuntimeError(str(result))` 重抛 ✅

**边缘问题：** 当 `fn()` 返回 `None` 时，`json.dumps(None)` 输出 `"null"`，反序列化后 `json.loads("null")` 返回 `None`。缓存命中时 `cached is not None` 为 `True`，所以 `None` 作为有效返回值可以正常缓存和恢复。✅ 但 `result is None` 时 `if cached is not None` 判断正确——没有 bug。

**真正的边界问题**：如果 `fn()` 的返回值是一个**不可 JSON 序列化的对象**，`put_activity` 会抛出 `TypeError`。此时异常不会被 `activity()` 中的 `except Exception` 捕获（因为它发生在 `put_activity` 调用中，不在 try 块内）。这会导致 `_exec_node` 的 retry 逻辑触发。修复方案：将 `put_activity` 放入 try 块，或增加预检查。

但当前场景下 LLM 返回 `str`，这个风险不存在。作为 API 设计，可以考虑在文档中明确约束，或在 `put_activity` 中做类型检查。

### 发现的问题（按严重程度排列）

#### 问题 1（medium）：回环场景中 activity 缓存导致节点结果不更新

**位置**：`agentflow/graph.py` — `NodeContext.activity()`

**描述**：在回环场景（debugger → coder 循环）中，coder 节点每次重入时 state 不同（`test_failures` 变化），但 `activity()` 按 `(thread_id, node, activity_key)` 缓存，第二次进入 coder 时会命中第一次执行写入的缓存，返回旧的 LLM 结果。

举例说明执行流程：
```
super-step 3: coder 执行 → activity("llm_complete", fn) 写入缓存
    └─ 缓存: (tid, "coder", "llm_complete") = "针对旧 feedback 的代码"
super-step 4: debugger → 发现失败 → 路由回 coder
super-step 5: coder 再次执行 → activity("llm_complete", fn) 命中缓存
    └─ 返回旧的代码，没有使用新的 feedback
```

在当前 demo 中，因为 mock LLM 的输出不依赖 prompt（始终返回固定格式），所以看起来没问题。但接入真实 LLM 后，回环中的 Coder 每次都会返回**第一次生成的结果**，debugger 永远发现同样的失败，形成死循环（直到 max_steps）。

**根本原因**：activity 缓存的粒度是 `(thread_id, node, key)`，但回环场景中**同一节点在同一 thread 内的多次执行需要不同的缓存条目**——因为每次执行时的 state 上下文不同。

**建议修复方案**（三选一）：
1. **按 step 区分**：缓存键改为 `(thread_id, node, step, key)`，但需要 `activity()` 能拿到当前 step（`NodeContext.step` 已存在）。这样同一节点不同 step 的调用各自独立缓存。
2. **按 state hash 区分**：在 key 中附加 state 的某种指纹，但过于复杂，不推荐。
3. **不缓存**：对于回环中依赖 state 变化的节点（如 coder 的 prompt 依赖 feedback），不包裹 activity。但这样会丢失中断恢复场景的缓存收益。

**推荐方案 1**：将缓存键改为 `(thread_id, node, step, activity_key)`。`NodeContext` 已持有 `step` 字段，只需在 `activity()` 中将其加入缓存查询即可。

#### 问题 2（minor）：异常缓存丢失异常类型

**位置**：`agentflow/graph.py:81` — `activity()` 方法

```python
self._cp.put_activity(self.thread_id, self.node, key, str(exc), "exception")
```

`str(exc)` 只保留异常消息（如 `"模拟失败"`），丢失了原始异常类型（如 `ValueError`、`KeyError`）。重入时用 `RuntimeError(str(result))` 重抛，类型信息丢失。

**影响**：低。当前所有节点都统一 catch `Exception`，不看异常类型。但如果将来有调用方需要区分异常类型做不同处理，会受到影响。

**建议修复**：存储 `{"type": type(exc).__name__, "message": str(exc)}` 的 JSON 字符串，重抛时使用 `_EXC_MAP.get(exc_type, RuntimeError)(message)` 恢复原始异常类型。

#### 问题 3（minor）：`put_activity` 的异常不在 try 块内

**位置**：`agentflow/graph.py:76-77`

```python
try:
    result = fn()
    self._cp.put_activity(...)  # 如果 fn() 返回不可序列化对象，这里抛 TypeError
    return result
```

`put_activity` 在 try 块内，但其抛出的 `TypeError`（`fn()` 返回值不可 JSON 序列化）会被 `except Exception` 捕获，然后**错误地**被当作 `fn()` 的异常缓存到 `activity_results` 表中。即 `fn()` 实际执行成功了，但缓存记录 `status="exception"`。

**影响**：低。当前场景 fn() 返回 str，不会触发。但如果将来 activity() 用于缓存其他函数返回值，需要留意。

**建议修复**：将 `put_activity` 移出 try 块，或增加 try/except 嵌套：

```python
try:
    result = fn()
except Exception as exc:
    self._cp.put_activity(..., str(exc), "exception")
    raise
self._cp.put_activity(..., result, "success")
return result
```

#### 问题 4（style）：`test_activity.py` 使用模块级全局变量

**位置**：`test/test_activity.py:19-21`

```python
call_count: Counter = Counter()
```

模块级 `Counter` 在测试间共享，依赖 `reset_counts()` 手动清理。如果某个测试提前 return 或抛异常，`reset_counts()` 不会执行，会影响后续测试。

**影响**：低。当前 7 个测试都正确调用了 `reset_counts()`，且测试顺序是线性的。但如果将来扩展测试或改为 pytest 随机顺序执行，可能出现交叉污染。

**建议修复**：用 pytest fixture 替代模块级全局变量，或在每个测试的 setUp 中重置。

### 亮点

- **API 设计简洁**：`ctx.activity(key, fn)` 接口只有两个参数，使用方只需把原有调用包一层 lambda，心智负担低
- **退化路径完善**：无 checkpointer 时退化为直接调用 `fn()`，不影响非持久化场景
- **异常缓存设计正确**：异常也被缓存，重入时不重试失败的调用，避免反复调用失败的 LLM
- **测试覆盖全面**：7 个测试覆盖了首次执行、缓存命中、不同 key、不同 thread、异常、复杂类型、无 checkpointer 退化
- **并发安全**：使用已有 `_lock` 串行化所有 activity 操作，无新锁引入

### 最终结论

**需修复问题 1 后合并。** 问题 1（回环场景缓存导致节点结果不更新）是中等严重程度的问题——demo 因 mock LLM 的确定性输出未暴露此问题，但接入真实 LLM 后会表现为回环中 coder/debugger 每次返回相同结果，导致无法收敛。建议在缓存键中加入 `step` 维度区分同一节点在不同 step 的调用。

问题 2-4 为 minor 级别，可一并修复或记录为技术债。代码整体质量高，API 设计简洁，测试覆盖充分。

---

## P0-2 审查 — 2026-06-17

### 审查范围

`feat/p0-2-tool-calls` 分支（commit `cae8996`），实现工具调用持久化：tool_calls 表 + log_tool_call() + 自动记录。改动涉及 `agentflow/checkpoint.py` (+57 行)、`agentflow/graph.py` (+45 行)、`test/test_activity.py` (+80 行)。

### 测试结果

| 测试套件 | 结果 |
|---|---|
| `test/test_activity.py` | **10/10 通过**（原有 7 个 + 新增 3 个：tool_calls_logged、tool_call_summary、not_logged_on_cache_hit） |
| `test/test_invariants.py` | **2/2 通过，无回归** |
| `python3 demo.py` | **5/5 场景正常** |

### 发现的问题

#### 问题 1（minor）：`_make_output_summary()` 对 `bytes`、`set` 等类型兜底不充分

**位置**：`agentflow/graph.py:147-160`

```python
@staticmethod
def _make_output_summary(result: Any) -> str:
    if isinstance(result, str):
        return result[:100]
    if isinstance(result, dict):
        return f"dict(keys={list(result.keys())})"
    if isinstance(result, list):
        return f"list(len={len(result)})"
    if isinstance(result, tuple):
        return f"tuple(len={len(result)})"
    if result is None:
        return "None"
    return str(result)[:100]
```

`bytes` 类型经过 `str(result)[:100]` 会输出 `b'...'` 格式，对于二进制大对象（如图片 base64）可能长达 100 字符但仍不具可读性。建议在 `str` 检查之后、`dict` 检查之前增加 `bytes` 分支：`return f"bytes(len={len(result)})"`。

`set` 和 `frozenset` 类型会走 `str(result)[:100]`，输出类似 `{1, 2, 3}`，可读性尚可接受，非必须修复。

**影响**：极低。当前场景中 activity 返回的是 LLM 字符串输出，不会出现 `bytes` 类型。属于防御性增强建议。

#### 问题 2（minor）：`input_summary` 参数在 `activity()` 中仅传递，不参与缓存键

**位置**：`agentflow/graph.py:95-96`

`input_summary` 仅作为描述性参数传入 `log_tool_call()`，不参与 activity 缓存键计算。这意味着如果调用方两次以不同的 `input_summary` 调用同一个 `activity(key, fn)`，缓存仍会命中并返回第一次的结果，而 tool_call 记录中的 `input_summary` 是第一次的值。

**影响**：极低。`input_summary` 是调用方可选传入的描述，默认值为 `""`。当前所有节点都不传此参数（`nodes.py` 未改动），所以实际上所有记录的 `input_summary` 都是空字符串。此问题只在将来使用此参数时才有影响。

#### 问题 3（style）：`tool_call_summary()` 中 `failures` 命名与 `status` 值不完全对应

**位置**：`agentflow/checkpoint.py:232-233`

```python
"SUM(CASE WHEN status='exception' THEN 1 ELSE 0 END) AS failures "
```

SQL 中的 `status` 值当前仅有 `"success"` 和 `"exception"` 两种。但 SQL 统计 `failures` 时使用了 `CASE WHEN status='exception'` 的硬编码，如果未来扩展 `status` 取值（如 `"timeout"`、`"cancelled"`），这个统计会漏掉新的失败类型。

**影响**：极低。当前只有两种 status 值，逻辑完全正确。建议在将来扩展 status 时同步更新此统计，或者改为 `CASE WHEN status!='success' THEN 1 ELSE 0 END` 更加健壮。

#### 问题 4（edge case）：`test_tool_calls_logged` 断言 `seq == 0` 但未验证 seq 递增

**位置**：`test/test_activity.py:249`

```python
assert rec["seq"] == 0
```

单条记录的 seq 恒为 0，这个断言只验证了 seq 从 0 开始，但没有测试用例验证**多次调用时 seq 正确递增**。`test_tool_call_summary` 中的 `multi_call_node` 调用了两次 activity，可以补充验证 seq 分别为 0 和 1。

**影响**：低。seq 分配逻辑（`SELECT COALESCE(MAX(seq), -1) + 1`）与 `log_event` 共用同一模式，后者已在 `test_invariants.py` 中经过充分验证。属于测试覆盖增强建议。

### CR 重点审查点分析

#### 1. tool_calls 表结构 — 合理 ✅

```sql
CREATE TABLE IF NOT EXISTS tool_calls (
    thread_id     TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    node          TEXT NOT NULL,
    step          INTEGER NOT NULL,
    tool_name     TEXT NOT NULL,
    activity_key  TEXT NOT NULL,
    input_summary TEXT,
    output_summary TEXT,
    duration_ms   REAL NOT NULL,
    status        TEXT NOT NULL,
    ts            REAL NOT NULL,
    PRIMARY KEY (thread_id, seq)
)
```

- **seq 分配**：`SELECT COALESCE(MAX(seq), -1) + 1` 从当前 thread 最大 seq 计算下一个，与 `log_event()` 的 seq 分配逻辑一致 ✅
- **主键设计**：`(thread_id, seq)` 复合主键，seq 按 thread 独立分配，不同 thread 的 seq 互不影响 ✅
- **索引**：无显式索引。主键自动是 `(thread_id, seq)` 的聚集索引，`WHERE thread_id=?` 的查询都能利用这个索引 ✅
- **step 字段**：记录了节点执行时的 step 编号，可用于关联 checkpoint 历史 ✅

#### 2. `log_tool_call()` 的 `_lock` 使用 — 正确 ✅

```python
def log_tool_call(self, thread_id, node, step, ...):
    with self._lock:
        seq = self._conn.execute(...)
        self._conn.execute("INSERT INTO tool_calls VALUES ...")
        self._conn.commit()
```

- `_lock` 与 `put_activity()`、`put()`、`log_event()` 共用同一把锁，保证所有写操作串行化 ✅
- seq 的 `SELECT MAX` + `INSERT` 在锁内完成，不存在并发分配相同 seq 的风险 ✅
- `commit` 也在锁内，不会出现部分写入 ✅

#### 3. activity() 中缓存命中时不写 tool_calls — 正确 ✅

```python
cached = self._cp.get_activity(...)
if cached is not None:
    result, status = cached
    if status == "exception":
        ...
        raise
    return result  # ← 直接返回，不写 tool_calls
t0 = time.time()
try:
    result = fn()
    ...
    self._cp.log_tool_call(...)  # ← 只有首次执行才写
```

缓存命中的路径在 `return result` 之前没有调用 `log_tool_call()`，不会产生重复记录 ✅。测试 `test_tool_call_not_logged_on_cache_hit` 已验证：第二次 invoke 后 records 数量仍为 1 ✅。

#### 4. tool_call_summary() 聚合统计 — 正确 ✅

SQL 聚合：
- `COUNT(*) AS calls` — 总调用次数 ✅
- `ROUND(SUM(duration_ms), 2)` — 总耗时 ✅
- `ROUND(AVG(duration_ms), 2)` — 平均耗时 ✅
- `SUM(CASE WHEN status='success' THEN 1 ELSE 0 END)` — 成功次数 ✅
- `SUM(CASE WHEN status='exception' THEN 1 ELSE 0 END)` — 失败次数 ✅

边界情况：
- 无记录时：`GROUP BY node ORDER BY node` 返回空列表 ✅
- 全失败记录：successes=0, failures=N ✅
- duration_ms=0（fn 立即返回）：`ROUND(SUM(0), 2)` = 0.0，`ROUND(AVG(0), 2)` = 0.0 ✅
- 单一节点多次调用：`test_tool_call_summary` 验证了 2 次调用、calls=2、successes=2 ✅

#### 5. `_make_output_summary()` 对各类返回值的处理 — 基本健壮 ✅

覆盖类型：
- `str`：截取前 100 字符 ✅
- `dict`：输出 `dict(keys=[...])` ✅
- `list`：输出 `list(len=N)` ✅
- `tuple`：输出 `tuple(len=N)` ✅
- `None`：输出 `"None"` ✅
- 其他：`str(result)[:100]` 兜底 ✅

异常情况：
- 空 dict：`list(result.keys())` 返回 `[]`，输出 `dict(keys=[])` ✅
- 空 list：`list(len=0)` ✅
- 大字符串：`[:100]` 截断 ✅
- `str(result)` 抛出异常：理论上 `str()` 对任意 Python 对象都应该成功（至少返回 `<ClassName object at 0x...>`），不会 crash ✅

#### 6. NodeContext.activity() 新增 input_summary 参数 — 正确 ✅

```python
def activity(self, key: str, fn: Callable[[], Any],
             input_summary: str = "") -> Any:
```

- 默认值 `""`：现有代码调用 `ctx.activity(key, fn)` 不传 `input_summary` 也能工作 ✅
- `input_summary` 只在缓存未命中时传递到 `log_tool_call()`，不影响缓存键 ✅
- 文档字符串已更新，说明参数用途 ✅
- `nodes.py` 未做任何改动，完全向后兼容 ✅

### 亮点

- **设计简洁**：在 `activity()` 内部自动记录，对节点完全透明，`nodes.py` 无需改动
- **seq 分配与 log_event 一致**：复用同一模式（`COALESCE(MAX, -1) + 1`），学习成本低
- **异常路径记录完整**：fn() 抛出异常时也会写入 tool_call 记录，`status="exception"`，`output_summary` 含异常类型
- **缓存命中不重复记录**：逻辑正确，测试充分覆盖
- **`_make_output_summary` 类型覆盖全面**：str/dict/list/tuple/None 都有专门处理，兜底 `str(result)[:100]`
- **测试覆盖新增逻辑**：3 个新测试覆盖了写入验证、聚合统计、缓存命中不重复

### 最终结论

**审查通过。** 代码质量高，逻辑正确，所有测试通过（10/10 activity 测试 + 2/2 invariant 测试 + 5/5 demo 场景）。发现的 4 个问题均为 minor/边缘级别，不阻塞合并：

- 问题 1：`_make_output_summary` 对 `bytes` 类型可读性差（防御性建议）
- 问题 2：`input_summary` 不参与缓存键（设计如此，非 bug）
- 问题 3：`failures` SQL 统计用 `status='exception'` 硬编码（可改为 `!= 'success'` 更健壮）
- 问题 4：测试未验证 seq 递增（补充建议）

推荐合入 master。

---

## CR 审查 — c8d276b restore py37 debugger and graph validation compatibility（2026-06-19）

### 审查范围

Commit: `c8d276b30a6128b27fc7a60d25454d4ffcd79522`

改动文件：

- `agentflow/nodes.py`：将 Python 3.8+ 才有的 `shlex.join(test_files)` 替换为 Python 3.7 可用的 `" ".join(shlex.quote(path) for path in test_files)`。
- `agentflow/graph.py`：在 `_static_string_returns()` 中补充 `ast.Str` 分支，恢复 Python 3.7 下字符串 return 字面量提取能力。

`git show --stat --oneline c8d276b`：

- `agentflow/graph.py | 3 +++`
- `agentflow/nodes.py | 2 +-`
- 合计 2 files changed, 4 insertions(+), 1 deletion(-)

`git show --check c8d276b`：无 whitespace / conflict marker 问题。

### 测试结果

- `source /Users/ospacer/.py37/bin/activate && PYTHONPATH=. python -m pytest test/ -q`：104 passed
- 重点回归集 `test/test_debugger.py test/test_graph.py test/test_invariants.py -q`：30 passed

### 重点审查结论

1. Python 3.7 兼容性：PASS

`shlex.quote` 在 Python 3.7 标准库中可用，替换后不再依赖 `shlex.join`。本地 py37 全量测试确认 Debugger 相关失败已恢复。

2. Shell 参数 quoting 安全：PASS

`test_files` 来自 `os.walk(workdir)` 后的相对路径，逐个用 `shlex.quote()` 处理后再拼成 shell command。对空格、引号、分号、命令替换等 shell metacharacter 可正确转义，未发现 shell 注入回归。现有代码仍使用 `shell=True`，长期更稳妥的形态是 argv 形式 `subprocess.run(["pytest", ...])`，但本次修复未扩大既有风险。

3. 条件边 AST 分析：PASS

Python 3.7 下字符串字面量节点是 `ast.Str`，新增分支能让 `route_ghost()` 的 `return "ghost"` 被识别，从而恢复未定义节点 warning。该分支位于 `walk()` 内，外层仍通过手动 `visit()` 跳过 `FunctionDef` / `AsyncFunctionDef` / `Lambda` 子树，因此不会重新引入“嵌套函数 return 被误提取”的误报。现有 `test_conditional_returns_nested_function_not_extracted` 已覆盖。

4. Checkpoint / resume invariant：PASS

本次只改 Debugger 命令字符串构造和 validate 静态 AST 提取，没有触碰 `CompiledGraph`、frontier、checkpoint 写入或 resume 逻辑。`test/test_invariants.py` 已随全量测试和重点测试通过。

5. 测试提交情况：可接受

本 commit 未新增测试，但两个回归均已被现有测试直接覆盖：`test_debugger.py` 覆盖 py37 Debugger 执行路径，`test_graph.py::test_conditional_returns_undefined_node_is_warning` 覆盖条件边返回未定义节点 warning。修复前这些用例失败，修复后全量通过，本次可接受。

### Findings

无阻塞 findings。

### 最终结论

PASS。`c8d276b` 修复目标明确、范围小，Python 3.7 全量测试通过，未发现影响 checkpoint/resume invariant 的回归。建议 PM 可进入合并确认流程。

---

## CR 审查 — 603be83 Add JSON graph configuration（2026-06-19）

### 审查范围

Commit: `603be835ea60159baf7acc6620430653bfa80dd6`

重点审查文件：

- `agentflow/graph_config.py`
- `conf/graph_config.example.json`
- `demo.py`
- `test/test_graph_config.py`

`git show --stat --oneline 603be835ea60159baf7acc6620430653bfa80dd6`：

- `agentflow/__init__.py | 8 ++`
- `agentflow/graph_config.py | 178 ++++++++++++++++++++++++++++++++++++++`
- `conf/graph_config.example.json | 124 +++++++++++++++++++++++++++`
- `demo.py | 190 +++++++++++++++++++++--------------------`
- `test/test_graph_config.py | 173 +++++++++++++++++++++++++++++++++++++`
- 合计 5 files changed, 579 insertions(+), 94 deletions(-)

`git show --check 603be835ea60159baf7acc6620430653bfa80dd6`：无 whitespace / conflict marker 问题。

### 测试结果

- `source /Users/ospacer/.py37/bin/activate && PYTHONPATH=. python -m pytest test/ -q`：111 passed
- `source /Users/ospacer/.py37/bin/activate && python demo.py`：7 个 demo 场景全部执行完毕
- `source /Users/ospacer/.py37/bin/activate && PYTHON37=/Users/ospacer/.py37/bin/python ./scripts/verify_py37.sh`：全部通过，解释器 Python 3.7.17

### 重点审查结论

1. JSON schema 简单稳定：PASS

配置结构保持在 `graphs.<name>` 下声明 `max_steps`、`reducers`、`nodes`、`edges`、`conditional_edges`，节点支持字符串 shorthand 和对象形式，边支持二元数组和 `{from,to}` 对象。示例配置覆盖 pipeline、parallel、retry、timetravel、real_coder、real_debugger 六类图，能对应 demo 现有场景。

2. 无任意 import/eval：PASS

`graph_config.py` 只使用 `json.load` 读取配置，node/router/reducer 都来自调用方显式 registry。静态搜索未发现 `eval()`、`exec()`、`__import__`、`importlib`、`globals()`、`locals()` 等动态执行入口。

3. unknown registry entries：PASS

未知 node handler、router、reducer 均在构建阶段抛 `ValueError`。新增测试 `test_unknown_node_router_and_reducer_raise_value_error` 已覆盖三类失败路径。

4. demo 7 场景行为保持：PASS

`demo.py` 改为通过 `conf/graph_config.example.json` 构图后，pipeline 中断/恢复、并行 fan-out、retry、checkpoint history、LLM config 展示、real coder 写文件、real debugger pytest 回环均跑通。场景 7 最终 `tests_passed=True`，状态 `completed`。

5. Python 3.7 兼容：PASS

新增代码未使用 py38+ 语法。`verify_py37.sh` 在 Python 3.7.17 下通过 AST 兼容检查、invariant、activity、graph、planner、review、tools、coder、debugger、demo 全量验证。

6. checkpoint/resume invariant：PASS

本 commit 未修改 `CompiledGraph` resume/frontier/checkpoint 核心逻辑。`verify_py37.sh` 中 `test_no_rerun_on_resume` 通过，完整 pytest 也通过既有 invariant 测试。配置构建只生成 `StateGraph`，不改变 checkpoint/resume 语义。

7. 测试覆盖：PASS

`test/test_graph_config.py` 覆盖 JSON 读取、START/END alias、append reducer 并行 merge 顺序、conditional router registry、node retries、unknown node/router/reducer、以及可先构建未编译图再 validate 的用法。结合 `demo.py` 和既有全量测试，覆盖对本轮目标足够。

### Findings

无阻塞 findings。

### 最终结论

PASS。`603be83` 的 JSON 图配置化实现范围清晰，未引入动态代码执行风险，demo 重写后的 7 个场景保持可运行，Python 3.7 与 checkpoint/resume invariant 均通过验证。建议 PM 可进入合并确认流程。
