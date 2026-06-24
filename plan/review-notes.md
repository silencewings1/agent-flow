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

---

## CR 审查 — 未提交 diff：Python 3.14 validate + graph_config schema fix（2026-06-19）

### 审查范围

当前未提交 diff：

- `agentflow/graph.py`
- `agentflow/graph_config.py`
- `test/test_graph_config.py`

背景：Dev 修复两个问题：

1. Python 3.14 下 `ast.Str` 不存在导致 `StateGraph.validate()` 崩溃。
2. `graph_config` 未支持计划要求的 `nodes` 对象映射 + `fn` 字段。

### 测试结果

- `PYTHONPATH=. python3 -m pytest test/test_graph.py test/test_graph_config.py -q`：26 passed
- `PYTHONPATH=. python3 -m pytest test/ -q`：113 passed
- `PYTHONPATH=. /Users/ospacer/.py37/bin/python -m pytest test/ -q`：113 passed
- `/Users/ospacer/.py37/bin/python demo.py`：7 个 demo 场景全部执行完毕
- `PYTHON37=/Users/ospacer/.py37/bin/python ./scripts/verify_py37.sh`：第一次在 `test/test_activity.py:248` 的 `duration_ms > 0` 断言上偶发失败；单独复跑 `test/test_activity.py` 通过，随后完整复跑 `verify_py37.sh` 通过
- `git diff --check`：无输出

### 重点审查结论

1. Python 3.14 `ast.Str` 修复：PASS

`agentflow/graph.py` 改为通过 `getattr(ast, "Str", None)` 做兼容 guard。Python 3.14 下不会因为属性缺失崩溃；Python 3.7 下仍保留 `ast.Str` 路径。`test/test_graph.py` 中条件边静态返回分析相关用例通过。

2. JSON schema 兼容：PASS

`agentflow/graph_config.py` 新增 `_iter_node_specs()`，支持计划要求的 `nodes` 对象映射，同时保留既有 list schema。`_parse_node()` 支持 `fn` 作为 `handler` 别名；既有字符串节点 shorthand 和 list object `handler` schema 未被破坏。

3. 无任意 import/eval 风险：PASS

本次 diff 没有引入 `eval()`、`exec()`、`__import__`、`importlib`、`globals()`、`locals()` 等动态执行入口。节点、路由、reducer 仍只从显式 registry / 固定 reducer 表解析。

4. 测试覆盖：PASS

新增测试覆盖 `nodes` mapping + `fn`、list object + `fn`。既有测试继续覆盖 list string shorthand、list object `handler`、START/END alias、append reducer、conditional router、retries、unknown node/router/reducer、JSON-built graph validate。

5. checkpoint/resume invariant：PASS

本次 diff 未修改 `CompiledGraph` 的 checkpoint/frontier/resume 执行逻辑。全量 pytest 与 `verify_py37.sh` 中 invariant 测试均通过。

### Findings

- P2（非阻塞，既有测试脆弱性）：`scripts/verify_py37.sh` 首次运行时，`test/test_activity.py:248` 偶发触发 `assert rec["duration_ms"] > 0`。单独复跑和完整复跑均通过，且该问题不属于本次实现 diff，但说明验收脚本存在时间精度边界上的 flaky 风险。后续可将断言改为 `>= 0` 或让测试中的 activity 明确产生可测耗时。

### 最终结论

PASS。本次未提交 diff 修复目标明确，Python 3.7 和当前 `python3` 全量测试通过，demo 7 场景通过，未发现 P0/P1 阻塞问题，未发现 checkpoint/resume 回归或动态代码执行风险。

---

## CR 审查 — 未提交 diff：README.md 文档更新（2026-06-19）

### 审查范围

当前未提交 diff：

- `README.md`

### 轻量验证

- `git diff -- README.md`
- `rg -n "Python 3.8|5 个|reviewer|graph_config|test_graph_config|verify_py37|python3 demo.py" README.md`
- `python3 -m pytest test/test_graph_config.py -q`

### 审查结论

1. Python 3.7+ / 7 demo / 当前测试命令：PASS

README 已更新为明确支持 Python 3.7+，快速开始与“快速上手指南”都改为 `python demo.py`、`PYTHONPATH=. python -m pytest test/ -q`、`./scripts/verify_py37.sh` 这一套当前命令。`demo.py` 实际仍为 7 个场景，`python3 -m pytest test/test_graph_config.py -q` 也通过。

2. 过时内容清理：PASS

未在 README 中找到 `Python 3.8`、`5 个` 等旧表述；流水线描述也已从单一 reviewer 拓扑更新为 `ai_review` + `human_review` 分层。

3. JSON 图配置说明：PASS

README 对 `agentflow/graph_config.py` 和 `conf/graph_config.example.json` 的说明与实现一致：`graphs` 顶层、`nodes` 支持对象映射 + `fn`，也兼容 list / string / `handler`；节点和路由仅从显式 registry 解析；JSON 不会执行 `import` / `eval`。

4. 文档格式：PASS

本次 README 结构、标题层级、代码块和列表格式均无明显破损或排版错误。

### Findings

无 findings。

### 最终结论

PASS。README.md 当前 diff 与仓库现状一致，未发现与 Python 3.7+、7 个 demo、JSON graph config、`ai_review` / `human_review` 拓扑或测试命令相关的明显问题。

---

## CR 审查 — 未提交 diff：duration_ms flaky 最小修复 + plan 状态追加（2026-06-19）

### 审查范围

当前未提交 diff：

- `test/test_activity.py`
- `docs/plan.md`

背景：Dev 修复此前 CR 记录的 P2 flaky：`test/test_activity.py:248` 的 `duration_ms > 0` 在极快 activity 上偶发失败，现调整为 `duration_ms >= 0`；并在 `docs/plan.md:196-200` 追加当前完成状态。

### 测试结果

- `PYTHONPATH=. python3 -m pytest test/test_activity.py -q`：10 passed
- `PYTHONPATH=. python3 -m pytest test/ -q`：113 passed
- `PYTHON37=/Users/ospacer/.py37/bin/python ./scripts/verify_py37.sh`：PASS（Python 3.7.17，全量 verify 通过；输出中 `test_tool_calls_logged` 本次记录 `duration_ms=0.00`，验证 0ms 是实际可出现的合法边界）
- `git diff --check`：无输出

### 重点审查结论

1. `duration_ms >= 0` 语义：PASS

`duration_ms` 在 `agentflow/graph.py:136-152` 由同一次 activity 执行前后的时间差计算，并在 `agentflow/checkpoint.py:75` 以 SQLite `REAL NOT NULL` 存储。工具调用耗时语义上应为非负值；0ms 表示小于计时/格式化精度的极快调用，是合法边界。本次 Python 3.7 验证实际打印 `duration_ms=0.00`，说明旧断言 `> 0` 确实会误伤合法执行路径。`>= 0` 不会放过负数：若出现负值仍会失败。

2. 是否需要更强断言：当前最小修复可接受

显式增加 `isinstance(rec["duration_ms"], (int, float))` 可提升可读性，但不是本轮阻塞要求：`rec["duration_ms"] >= 0` 已会拒绝非可比较类型，且 `test/test_activity.py:251` 的 `:.2f` 格式化也会在非数值类型上失败。考虑本轮目标是修复 flaky，保持最小变更可接受。

3. `docs/plan.md` 完成状态：PASS

`docs/plan.md:196-200` 追加的是“当前完成状态（2026-06-19 进度）”，没有改写前文 P0 计划、协作流程或验收标准。内容与当前 diff 和此前 CR 记录一致：P0/P1、JSON 图配置、README 更新已完成；本轮 P2 flaky 已修；剩余建议仍以 `graph_config` example schema 和长期 P2/设计项为后续工作。未发现夸大为“所有长期建议完成”的问题。

### Findings

- P0：无。
- P1：无。
- P2：无。

### 最终结论

PASS。本轮 diff 是针对 P2 flaky 的最小修复，`duration_ms >= 0` 符合 tool_calls 非负耗时语义且仍能捕获负数异常；文档状态追加准确，未破坏历史计划。未发现 P0/P1 阻塞问题。

---

## CR 审查 — 当前 diff：graph_config canonical schema 最终复验（2026-06-19）

### 审查范围

当前未提交 diff：

- `README.md`
- `conf/graph_config.example.json`
- `docs/plan.md`

目标：确认示例 graph config 全量切换到 canonical `nodes` 对象映射 + `fn` 写法，README 不再展示或推荐旧 schema，plan 状态准确；同时移除本轮重复 CR 记录，仅保留本最终复验节。

### 测试结果

- `python3 -m json.tool conf/graph_config.example.json >/tmp/graph_config_check.json`：PASS。
- `/Users/ospacer/.py37/bin/python -c 'import json; json.load(open("conf/graph_config.example.json")); print("json ok")'`：PASS。
- `rg -n '"nodes"\s*:\s*\[|"handler"|list / string|也兼容|兼容 list|兼容旧配置' README.md conf/graph_config.example.json docs/plan.md`：PASS。仅命中 `docs/plan.md` 中“示例中不再保留 list / string / handler 兼容写法”的状态描述，未发现旧 schema 示例残留。
- `git diff --check`：PASS，无空白错误。
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m pytest test/ -q -p no:cacheprovider`：113 passed。
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python demo.py`：PASS，7 个 demo 场景全部执行完毕。
- `PYTHON37=/Users/ospacer/.py37/bin/python ./scripts/verify_py37.sh`：PASS。

### 重点审查结论

1. `conf/graph_config.example.json` canonical 写法：PASS

6 个 graph 的 `nodes` 均为对象映射，节点 spec 均使用 `fn`。`retry.flaky` 保留 `retries: 2`，`real_debugger.nodes.coder` 使用 `{"fn": "dummy_coder_fix_test"}`，符合本轮目标。

2. README 同步：PASS

README 的 JSON 图配置说明已改为示例配置统一采用对象映射 + `fn` 的规范写法，并给出带重试节点的 canonical 示例；未继续展示或推荐 list/string/handler 旧写法。实现层仍保留旧 schema 兼容能力，但当前文档变更范围是示例与推荐写法，二者不冲突。

3. `docs/plan.md` 状态：PASS

plan 追加的完成状态与当前 diff 一致：记录 example config 已全量切换、README 已同步、示例不再保留旧 schema 写法；未发现夸大完成范围或与实际文件不一致的问题。

4. `docs/review-notes.md` 当前轮记录：PASS

本轮重复追加的 graph_config CR 记录已合并为当前最终复验节，避免 PM/Dev 后续读取时重复判断同一轮结论。

### Findings

- P0：无。
- P1：无。
- P2：无。

### 最终结论

PASS。当前 diff 满足 graph_config 示例切换为 canonical `nodes` 对象映射 + `fn`、README/plan 同步、无旧 schema 示例残留的审查要求；Python 3.7 全量测试与 demo 均通过，未发现阻塞问题。

---

## CR 审查 — 当前 diff：graph_config validation/error enhancement（2026-06-19）

### 审查范围

当前未提交 diff：

- `agentflow/graph_config.py`
- `test/test_graph_config.py`
- `README.md`
- `docs/plan.md`

目标：独立确认 Dev 对 JSON graph config 的配置校验增强是否满足本轮要求，且不引入安全风险、demo 回归或 Python 3.7 兼容问题。

### 测试结果

- `git diff --check`：PASS，无空白错误。
- `PYTHONPATH=. python3 -m pytest test/test_graph_config.py -q`：31 passed。
- `PYTHONPATH=. python3 -m pytest test/ -q`：135 passed。
- `/Users/ospacer/.py37/bin/python demo.py`：PASS，7 个 demo 场景全部执行完毕。
- `PYTHON37=/Users/ospacer/.py37/bin/python ./scripts/verify_py37.sh`：PASS，Python 3.7.17 全量验证通过。

### 重点审查结论

1. schema 收紧：PASS

canonical `nodes` 对象映射现在要求每个 node spec 是对象且显式配置 `fn`；示例 schema 的推荐路径更稳定。既有 list shorthand / list object `handler` 兼容路径仍由测试覆盖，未被本轮误删。

2. 安全边界：PASS

`node` / `router` 仍只从调用方传入的显式 registry 解析。当前 diff 未新增任意 `import`、`eval`、动态模块加载或 JSON 代码执行路径。

3. 配置校验准确性：PASS

`max_steps` 必须是大于 0 的整数；`retries` 必须是非负整数；`retry_backoff` 必须是非负有限数字；`edges` / `conditional_edges` 顶层必须为数组；`conditional_edges.from` 禁止 `START` / `END`；未知 node/router/reducer 均抛带 graph 上下文的 `ValueError`。新增负例测试覆盖了本轮核心错误路径。

4. demo 与 checkpoint/resume invariant：PASS

全量 pytest 通过，包含 checkpoint/resume invariant；Python 3.7 demo 的 7 个场景全部通过。本轮改动未触碰 `checkpoint.py` / `graph.py` 执行器语义，未发现 resume replay 风险。

### Findings

- P0：无。
- P1：无。
- P2：无。

### 最终结论

PASS。本轮 diff 满足 graph_config validation/error enhancement 的审查要求，未发现阻塞问题；CR 仅追加了本节 `docs/review-notes.md` 记录，未修改实现代码。

---

## CR 审查 — 对抗性 Fuzz：graph_config.py + graph.py validate/compile（2026-06-19）

### 审查范围

已提交代码的对抗性 fuzz testing，目标：发现现有测试未覆盖的边界情况或逻辑缺陷。

重点文件：
- `agentflow/graph_config.py`（283 行）
- `agentflow/graph.py` — `validate()` / `compile()` / `add_conditional_edges()`

### 测试方法

使用 Python 脚本构造 37+ 个边界输入，调用 `build_state_graph_from_config()` / `build_graph_from_config()` / `StateGraph` 直接 API，观察是否正确报错或行为合理。

### 发现的问题

#### 问题 1（P1）：`conditional_edges.from` 不校验源节点是否已声明

**位置**：`agentflow/graph_config.py:183-197` — `_parse_conditional_edge()`

**复现**：
```python
config = {"graphs": {"g": {
    "nodes": {"a": {"fn": "a"}},
    "edges": [["START", "a"]],
    "conditional_edges": [{"from": "ghost", "router": "r"}],  # ghost 未声明
}}}
g = build_state_graph_from_config(config, "g", NODES, ROUTERS)
g.validate()  # → 0 errors!
g.compile()   # → OK，无报错
```

**对比**：静态边 `["a", "ghost"]` 在 `validate()` 中正确报 `边引用了未定义节点: a -> ghost`，但条件边不报。

**根本原因**：缺陷贯穿两层：

1. `graph_config._parse_conditional_edge()`：只校验 `from`/`router` 非空、`from` 不是 START/END、`router` 在 registry 中。不检查 `from` 对应的节点是否已通过 `add_node()` 注册。
2. `graph.py validate()` 第 283-286 行：只检查条件边 router 是否 callable，不检查条件边源节点是否在 `self._nodes` 中。
3. `graph.py compile()` 第 252-258 行：只检查 `_entry` 和 `_edges` 中的引用，完全遗漏 `_cond` 的键。

**建议修复**（至少在 graph_config 层修复，graph 层可选）：

`graph_config.py` — 在 `build_state_graph_from_config()` 解析完所有 nodes 后，增加 collected nodes set，在处理 conditional_edges 时校验 `from` 节点是否在 set 中：

```python
# 在 node 循环后收集
declared_nodes = set()
for node_spec in _iter_node_specs(...):
    node = _parse_node(node_spec, graph_name)
    declared_nodes.add(node["name"])
    ...

# 在 conditional_edges 循环中增加校验
for index, cond_spec in enumerate(...):
    cond = _parse_conditional_edge(cond_spec, graph_name, index)
    if cond["from"] not in declared_nodes:
        raise ValueError(
            f"graph {graph_name} conditional_edges[{index}] "
            f"引用了未定义节点: {cond['from']}"
        )
    ...
```

`graph.py` — 在 `validate()` 第 2 项检查中增加条件边源节点校验：

```python
# 2) 条件边函数必须可调用，且源节点必须存在
for src, router in self._cond.items():
    if src not in self._nodes:
        issues.append(ValidationIssue(
            "error", f"条件边引用了未定义节点: {src}", node=src))
    if not callable(router):
        ...
```

`graph.py` — 在 `compile()` 中增加 `_cond` 键的检查：

```python
referenced = set(self._entry)
for outs in self._edges.values():
    referenced.update(outs)
referenced.update(self._cond.keys())  # 新增
```

#### 问题 2（P2）：同一节点既有静态出边又有条件出边时，静默覆盖无警告

**位置**：`agentflow/graph.py:246-249` — `add_conditional_edges()`

**复现**：
```python
g.add_edge("a", "b")            # a -> b 静态边
g.add_conditional_edges("a", r) # a -> router 条件边
```

实际执行时条件边覆盖静态边（`b` 不会被执行），但 `validate()` 返回 0 个 warning/error。

**影响**：低。这是 LangGraph 的设计语义（后添加的覆盖先添加的），但 graph_config 层可以给个 warning 提醒用户可能不是预期行为。

#### 问题 3（P2）：`validate()` 错误消息中泄漏内部节点名 `__end__` / `__start__`

**位置**：`agentflow/graph.py` — `validate()` 各处 + `graph_config._format_validation_errors()`

**复现**：当用户在 JSON 中使用 `END` alias 但配置出错时，error 消息可能显示 `a -> __start__` 而非 `a -> START`。

**影响**：低。`__start__` / `__end__` 是内部 sentinel 值，用户不应直接看到。建议在 `_format_validation_errors()` 中做替换，或在 `validate()` 中统一使用用户可见名称。

### 已通过验证的防御点（37 项 fuzz 全部处理正确）

| 类别 | 测试场景 | 结果 |
|------|---------|------|
| 空值/类型 | nodes=null, edges=null, cond=null, graph_spec=数组, graphs=数组 | 全部 ValueError |
| 边界值 | max_steps=0/-1/True, retries=-1/2.5, retry_backoff=-0.1/inf/nan | 全部 ValueError |
| START/END | cond.from=START/END/内部值, 边中用内部值 | 正确拦截 |
| 字段缺失 | cond 缺 from/router, nodes mapping 缺 fn, graph name 不存在 | 全部 ValueError |
| 特殊类型 | retry_backoff 为字符串数字("0.5"), reducers key 为整数 | 正确接受 |
| 运行时 | router 返回不存在的节点名 → KeyError（运行时崩溃） | 可接受（静态分析 warning 已覆盖） |
| 结构 | 空 graph, 孤岛节点, 无入口 | validate 正确报告 |

### 最终结论

**P1 × 1，P2 × 2。** 问题 1（conditional_edges.from 不校验源节点）是明确的验证遗漏，静态边有校验但条件边没有，属于对称性缺陷，建议修复。问题 2、3 为增强建议，不阻塞但值得记录。

---

## CR 复验 — 3b6da1b conditional_edges.from 源节点校验修复（2026-06-20）

### 审查范围

commit `3b6da1b` 相对 `origin/master` 的修复 diff：

- `agentflow/graph.py`
- `agentflow/graph_config.py`
- `test/test_graph.py`
- `test/test_graph_config.py`
- `docs/review-notes.md`

目标：复验上一节对抗性 fuzz 发现的 P1：`conditional_edges.from` 指向未声明节点时，JSON 构建、`StateGraph.validate()`、`StateGraph.compile()` 都没有报错。

### 测试结果

- `git diff --check origin/master..HEAD`：PASS，无空白错误。
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m pytest test/test_graph.py test/test_graph_config.py test/test_invariants.py -q -p no:cacheprovider`：54 passed。
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m pytest test/ -q -p no:cacheprovider`：139 passed。
- `PYTHONDONTWRITEBYTECODE=1 PYTHON37=/Users/ospacer/.py37/bin/python ./scripts/verify_py37.sh`：PASS，解释器 Python 3.7.17；demo 7 个场景全部执行完毕，场景 7 最终 `status=completed`、`tests_passed=True`。
- 补充对抗性复现脚本：PASS，直接验证 `StateGraph.add_conditional_edges("ghost", ...)` 在 `validate()` 与 `compile()` 均失败，JSON config 的 `conditional_edges.from="ghost"` 也失败。

### 重点审查结论

1. `graph_config.py` 构建期校验：PASS

构建节点时收集 `declared_nodes`，解析 `conditional_edges` 后用原始 config 节点名校验 `from` 是否已声明。该逻辑能区分 node name 与 handler name，覆盖了 `{"my_node": {"fn": "a"}}` 时误把 `"a"` 当节点名的情况；`START` / `END` 仍由 `_parse_conditional_edge()` 拦截。

2. `StateGraph.validate()` 静态校验：PASS

`validate()` 现在对 `_cond` 的源节点执行存在性检查，`add_conditional_edges("ghost", router)` 会产生 error。该检查在条件边 router callable 检查之前执行，不影响既有非 callable router 报错路径。

3. `StateGraph.compile()` 最终防线：PASS

`compile()` 将 `_cond.keys()` 加入 referenced 集合，即使调用方绕过 JSON 配置层且不主动调用 `validate()`，未定义条件边源节点也会抛 `ValueError`。这与静态边引用检查保持一致。

4. 测试覆盖：PASS

新增测试覆盖了直接 `StateGraph` API、JSON config 未定义源节点、handler name 不等于 node name、空 nodes 列表等关键场景。目标测试、全量 pytest、Python 3.7 全量验证均通过。

### Findings

- P0：无。
- P1：无。
- P2：无。

### 最终结论

PASS。`3b6da1b` 已修复 `conditional_edges.from` 未校验源节点的 P1 缺陷，三层防线（config 构建、validate、compile）均生效，未发现 checkpoint/resume invariant、Python 3.7 兼容性或 demo 回归。建议 PM 可进入状态文档更新与同步流程。

## P2-1 Send/Worker CR — codex/p2-send-worker（2026-06-20）

### 结论

**FAIL。** 指定检查命令全部通过，但独立 CR 在 Send worker + interrupt/resume 路径发现违反项目硬不变量的问题：同一 Send worker super-step 中，某个 worker 中断时，已经完成的同批 worker 会在 resume 后被重跑。该问题会导致非幂等 worker 的副作用重复执行，且与 AGENTS.md / checkpoint 设计中的“resume never replays completed nodes / 已完成节点绝不重跑”不一致。

### 已执行检查

- `git diff --check master..HEAD`：PASS。
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m pytest test/ -q -p no:cacheprovider`：PASS，`154 passed in 3.29s`。
- `PYTHONDONTWRITEBYTECODE=1 PYTHON37=/Users/ospacer/.py37/bin/python ./scripts/verify_py37.sh`：PASS，Python `3.7.17`，脚本内全量测试与 demo 均通过。
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python demo.py`：PASS，8 个场景全部执行完毕。

### 审查范围与重点结论

- 已读取 `AGENTS.md`、`docs/plan-p2.md`、`docs/review-checklist-p2-send.md`。
- 已查看 `git diff master..HEAD`。本分支当前为 `codex/p2-send-worker`，HEAD 为 `9475b29 docs: record P2 send PM handoff`，核心实现提交为 `da67cee feat: add dynamic Send worker execution`。
- 动态 Send 基本行为、`Send.arg` 注入隔离、`fanout_reducer`、JSON `fanout` reducer 与多源 barrier 基本路径在现有测试/demo 中通过；但 checkpoint resume 中断路径未满足硬不变量。

### Findings

#### P1：Send worker 批次中断后，已完成的同批 worker 会在 resume 后重跑

**位置**：`agentflow/graph.py`，`CompiledGraph._run_loop()` interrupt 处理路径。

当前 super-step 并行执行 `batch` 后按 `futures` 插入顺序取结果。一旦某个 future 返回 `interrupt`，实现会把 `current_frontier = waiting + batch` 整批写入 checkpoint，并直接返回 interrupted：

- checkpoint state 仍是本轮执行前的 state；
- frontier 包含整个 batch；
- 该 batch 中在 interrupt 之前已经完成且产生副作用/日志/activity 的 worker 没有从 frontier 中移除；
- resume 时会从 checkpoint frontier 重跑整批 worker。

这对普通静态并行节点也可能存在，但 P2 Send/worker 让同名动态 worker 批量执行成为核心路径，因此此分支必须明确处理或文档化并规避。按本仓库硬约束“frontier only contains nodes yet to run / resume never replays completed nodes”，当前行为应视为阻塞合并的问题。

**复现方式**（临时脚本执行，不修改实现代码）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python - <<'PY'
from __future__ import print_function
import time
from collections import Counter
from agentflow import StateGraph, StateSchema, START, Send, Checkpointer, Command, append_reducer

calls = Counter()

def start(state, ctx):
    return {"items": [1, 2]}

def route(state):
    return [Send("worker", {"item": 1}, key="one"), Send("worker", {"item": 2}, key="two")]

def worker(state, ctx):
    item = state["item"]
    calls[item] += 1
    if item == 1:
        time.sleep(0.2)  # 让 item=2 先完成
        ctx.interrupt({"item": item, "calls_snapshot": dict(calls)})
    return {"seen": [item]}

g = StateGraph(StateSchema(reducers={"seen": append_reducer}))
g.add_node("start", start)
g.add_node("worker", worker)
g.add_edge(START, "start")
g.add_conditional_edges("start", route)
app = g.compile(Checkpointer())

r1 = app.invoke({}, thread_id="cr-send-interrupt-rerun")
print("r1", r1.status, r1.interrupt_payload, "calls", dict(calls))
r2 = app.invoke({}, thread_id="cr-send-interrupt-rerun", command=Command(resume="ok"))
print("r2", r2.status, r2.state, "calls", dict(calls))
PY
```

**实际输出**：

```text
r1 interrupted {'item': 1, 'calls_snapshot': {1: 1, 2: 1}} calls {1: 1, 2: 1}
r2 completed {'items': [1, 2], 'seen': [1, 2]} calls {1: 2, 2: 2}
```

`item=2` 在第一次返回 interrupted 前已经执行过一次，resume 后又执行一次；`item=1` 也因恢复中断节点而执行第二次。`seen` 只记录恢复后的结果，是因为 interrupt checkpoint 保存的是旧 state，无法反映已完成 sibling 的 update。

**建议修复**：

- 在 interrupt/error 处理时，区分 batch 内已完成且已成功的 item 与尚未完成/中断的 item；checkpoint frontier 只保留未完成或需要 resume 的 item，避免已完成 item 重跑。
- 若要保留“super-step 原子提交”语义，则需要明确禁止/取消同 batch sibling 在 interrupt 时产生不可回滚副作用，并在文档与测试中声明；但这会弱化当前项目硬不变量，不建议作为 P2-1 合并方案。
- 增加回归测试：Send router 产生两个 worker，其中一个延迟后 interrupt，另一个先完成；断言 resume 后先完成 worker 的调用计数仍为 1，且 checkpoint frontier 不包含已完成 worker。

### 补充观察（非阻塞）

- 重复 Send key：`[Send("w", {"id": 1}, key="x"), Send("w", {"id": 2}, key="x")]` 不 crash，但两个 Send 生成相同 `instance_id` 后被 `_dedupe_items()` 去重，实际只执行第一个 worker，最终状态为 `{'seen': 1}`。清单要求“至少不能 crash”，当前满足；建议后续文档明确重复 key 语义，或在开发模式给出 warning/error。
- barrier 等待项会以 frontier dict 形式持久化，JSON checkpoint 可序列化；现有 strict barrier 与 JSON barrier 测试覆盖基本路径。
- `Send.arg` 不自动写回全局 state，worker 通过 reducer 返回 update 合并；现有测试覆盖该行为。
- `graph_config.py` 仍通过显式 registry 解析 node/router/reducer，没有引入 JSON 动态 import/eval。

### 最终结论

**FAIL。** P1 × 1：Send/worker 中断恢复路径会重跑同批已完成 worker，违反 checkpoint/resume “已完成节点不重跑”硬不变量。建议 Dev 修复并新增上述回归测试后再提交 CR 复验；PM 不应合并当前分支。

## P2-1 Send/Worker CR 复验 — 220066b（2026-06-20）

### 结论

**PASS。** 最新提交 `220066b fix: avoid rerunning completed send workers on resume` 已修复上一轮 P1：Send worker 批次中断后，已完成 sibling worker 不再在 resume 后重跑。PM 可进入合并确认流程。

### 已执行检查

- `git diff --check master..HEAD`：PASS，无输出。
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m pytest test/test_send.py test/test_invariants.py -q -p no:cacheprovider`：PASS，`12 passed in 0.47s`。
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m pytest test/ -q -p no:cacheprovider`：PASS，`155 passed in 3.91s`。
- `PYTHONDONTWRITEBYTECODE=1 PYTHON37=/Users/ospacer/.py37/bin/python ./scripts/verify_py37.sh`：PASS，Python `3.7.17`；脚本内 py37 兼容性、不变量、activity、graph、planner、Send/worker、review、tools、coder、debugger 与 8 个 demo 场景均通过。

### 复验范围

- 已阅读 `AGENTS.md`，确认 checkpoint/resume 硬不变量：frontier 只包含尚未执行节点，resume 不应重放已完成节点。
- 已阅读 `docs/review-notes.md` 末尾上一轮 `P2-1 Send/Worker CR — codex/p2-send-worker（2026-06-20）` 的 FAIL 记录。
- 已查看 `git diff HEAD~1..HEAD`：实现改动集中在 `agentflow/graph.py`，测试改动集中在 `test/test_send.py`。

### 重点复验结果

1. 快 worker 不再重跑：PASS

使用上一轮 CR 复现脚本的等价脚本验证：第一次运行中 `item=2` 快 worker 已完成并写入 `seen`；resume 后调用计数保持 `item=2: 1`，未再次执行。中断 worker `item=1` 因注入 `Command(resume="ok")` 正常恢复执行第二次。

复验输出摘要：

```text
r1 interrupted {'items': [1, 2], 'seen': ['fast:2']} ... calls {1: 1, 2: 1}
frontier [{'kind': 'node', 'node': 'worker', 'instance_id': 'worker:3f7c3847e0c9', 'arg': {'item': 1}}]
r2 completed {'items': [1, 2], 'seen': ['fast:2', 'slow:ok']} calls {1: 2, 2: 1}
```

2. checkpoint frontier 不含已完成 sibling：PASS

中断 checkpoint 的 frontier 仅保留慢 worker：`{'arg': {'item': 1}}`。已完成的快 worker `{'item': 2}` 已从 resume frontier 移除，符合“frontier only contains nodes yet to run”。

3. 最终 state 保留已完成 sibling update 与中断 worker 恢复 update：PASS

第一次 interrupted 返回的 state 已包含快 worker update：`seen == ['fast:2']`；resume 完成后的最终 state 为 `seen == ['fast:2', 'slow:ok']`，既保留已完成 sibling 的 partial commit，也合并了中断 worker 恢复后的 update。

4. 合并顺序与现有不变量：PASS

修复在 interrupt 路径按原 batch 顺序合并成功 sibling updates，而不是按完成顺序合并；目标测试、全量 pytest、`test/test_invariants.py` 与 py37 全量验证均通过，未发现既有 no-rerun invariant 回归。

### Findings

- P0：无。
- P1：无。
- P2：无。

### 最终结论

**PASS。** `220066b` 已修复 Send/worker 中断恢复路径重跑已完成 sibling worker 的 P1 问题，并新增 `test_send_interrupt_commits_completed_sibling_without_rerun` 回归测试覆盖快 worker 不重跑、checkpoint frontier 排除已完成 sibling、最终 state 保留两侧 update。PM 可进入合并确认流程。
