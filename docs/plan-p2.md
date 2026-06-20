# P2 开发计划

> 基于 `docs/agent-flow-analysis-report.md` 第 5 节，P0 补齐可靠性、P1 补齐真实研发能力，P2 聚焦**扩展编排能力**：动态扇出、显式汇聚、子图嵌套、外部工具协议。

---

## P2 任务总览

| 编号 | 事项 | 权重 | 目标 | 预计工作量 | 依赖 |
|------|------|------|------|------------|------|
| P2-1 | 动态 Send/worker | 3 | router 动态生成 N 个 worker，每个带独立 state slice | 2-3 天 | 无 |
| P2-2 | join/barrier | 1 | 动态 worker 全部完成后显式汇聚 | 0.5-1 天 | 已并入 P2-1 本轮范围 |
| P2-3 | 子图 | 1 | 节点可以是嵌套 StateGraph | 1-2 天 | 无 |
| P2-4 | MCP 工具适配 | 0 | 预留外部工具协议接口 | 0.5 天 | 无 |

4 个任务**完全独立**，可并行 4 个 Dev 窗口。

---

## P2-1：动态 Send/worker（权重 3）

### 问题

当前条件边 `router(state) -> "node_name"` 只能返回**已注册的静态节点名**或**静态节点名列表**（固定扇出）。无法实现：
- "根据 LLM 输出动态生成 N 个代码审查 worker，每个审查不同文件"
- "把 plan.tasks 的每个 task 动态扇出到一个独立 coder worker"

当前 demo 场景 2（并行扇出）是**静态的** — 3 个 worker 在编译时已确定。

### 方案

引入 `Send` API：**条件边 router** 返回 `[Send("worker", {"task": t1}), Send("worker", {"task": t2})]`，引擎在下一个 super-step 中为每个 Send 创建一个 worker 实例，各自带独立 state slice。普通节点函数仍只返回 partial state update（`dict` / `None`），不扩展节点返回协议。

**核心概念**：

```python
@dataclass
class Send:
    node: str          # 目标节点名（已注册）
    arg: Dict[str, Any]  # 注入该 worker 实例的 state slice
    key: Optional[str] = None  # 可选稳定 key；未传时由引擎基于 arg/index 派生
```

**数据流**：
```
planner → [Send("coder", {"task": t1}), Send("coder", {"task": t2})]
              │                              │
              ├─ coder(state+{"task": t1})   │  ← 同一 super-step 并行
              │                              │
              └─ coder(state+{"task": t2})   │
                     │                       │
                     └─── 汇聚到 join ───────┘
```

### 改动文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `agentflow/graph.py` | `Send` dataclass + `CompiledGraph._run_loop` 支持 Send fan-out + 对象 frontier + barrier | ~300 行 |
| `agentflow/state.py` | 新增 `fanout_reducer`：合并 `{instance_id: payload}` dict | ~20 行 |
| `agentflow/__init__.py` | 导出 `Send` | +2 行 |
| `agentflow/graph_config.py` | reducers 支持 `"fanout"`，edges 支持 `{"from": [...], "to": "join"}` | ~40 行 |
| `conf/graph_config.example.json` | 新增 `dynamic_send` 示例；parallel 改用多源 barrier | ~30 行 |
| `demo.py` | 新增场景 8：动态 Send worker | ~50 行 |
| `test/test_send.py` | 新增 | ~280 行 |

### 关键设计

| 决策点 | 方案 | 理由 |
|--------|------|------|
| Send 返回格式 | router 返回 `[Send("node", {"key": val}), ...]` | 与当前条件边调度模型对齐，避免扩大 NodeFn 协议 |
| 引擎处理 | super-step 中检测 Send → 展开为多个 worker 实例 → 下一个 super-step 并行执行 | 保持 super-step 语义 |
| State 注入 | `Send.arg` 只注入该 worker 的运行态，不自动写回全局 state | worker 间 state 隔离，避免多实例覆盖同名 key |
| 实例标识 | 引擎生成稳定 `instance_id`，`NodeContext.instance_id` 可读 | 同名 worker 多实例的日志和 cache 可区分 |
| Activity cache | Send worker 自动把 `instance_id` 并入 activity key | 防止同名 worker 多实例撞缓存 |
| 结果汇聚 | 每个 worker 返回 `{key: {ctx.instance_id: payload}}`，用 `fanout_reducer` 合并 | 保留所有 worker 产出 |
| Frontier 持久化 | checkpoint frontier 统一为对象记录；旧字符串 frontier 兼容归一化 | 支撑 Send/barrier resume，不破坏旧 checkpoint |
| Barrier | `add_edge(["a", "b"], "join")` 是严格 barrier，支持跨 super-step 等待 | join 只在所有来源完成后调度 |
| 静态扇出兼容 | `["a", "b"]` 仍工作（等于是 Send 的简化形式） | 向后兼容 |

### 测试用例

1. 节点返回 `[Send("w", {"id": 1}), Send("w", {"id": 2})]` → 2 个 w 实例并行执行
2. 每个 worker 的 `state["id"]` 不同（state slice 注入正确）
3. worker 返回的 partial update 用 fanout_reducer 合并
4. Send 指向不存在的节点 → 引擎报错
5. 空 Send 列表 → 行为等同返回 `[]`（图终止）
6. 混合 Send + 静态节点名 → 引擎正确处理
7. checkpoint/resume 后 Send worker `instance_id` 稳定，已完成节点不重跑
8. 同名 worker 多实例的 activity/tool cache 不互相命中
9. 严格 barrier：`add_edge(["a", "b"], "join")` 等 a/b 都完成才调度 join
10. JSON `{"from": ["a", "b"], "to": "join"}` 构图和非法节点校验

---

## P2-2：join/barrier（权重 1）

### 问题

当前图是"所有同级节点并行 → 全部完成后进入下一 super-step"，这已经是隐式 barrier。但**动态 Send 产生 N 个 worker 后，需要一个显式 join 节点来汇聚结果**。没有显式 join，只能靠"所有 worker 都指向同一个下游节点"来隐式汇聚，不够灵活。

### 方案

`add_edge(["w1", "w2", "w3"], "join")` — 支持多源单目标边，语义是"这些节点全部执行完后，进入 join"。该功能已纳入 P2-1 本轮实现范围，作为动态 Send/worker 的汇聚基础设施。

**简化实现**：P2-1 的 Send 已隐含 barrier（引擎等所有 Send worker 完成才继续），所以 P2-2 主要工作是：
1. 支持 `add_edge(["a", "b"], "c")` 多源边语法
2. 在 `to_mermaid()` 中渲染汇聚边
3. 文档化 barrier 语义

### 改动文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `agentflow/graph.py` | `add_edge` 支持 `src: Union[str, List[str]]` | ~15 行 |
| `test/test_graph.py` | 新增 barrier 测试 | ~20 行 |

### 测试用例

1. `add_edge(["a", "b"], "c")` → a 和 b 都执行完才执行 c
2. `to_mermaid()` 渲染多源汇聚边

---

## P2-3：子图（权重 1）

### 问题

当前节点是 Python 函数，无法在节点内部再编排 DAG。这限制了复用性 — 如果一个子流程有 3 步（如 "读文件 → 分析 → 写报告"），当前只能写成一个函数或 3 个平铺节点。

### 方案

`StateGraph.add_subgraph(name, subgraph, input_map, output_map)` — 把一个已编译的 `CompiledGraph` 注册为一个节点。父图调用子图时：
1. `input_map` 从父 state 提取子图需要的字段
2. 子图独立运行（有自己的 super-step 循环、checkpointer、max_steps）
3. 子图完成后，`output_map` 把子图 state 的指定字段写回父 state

```python
sub = StateGraph()
sub.add_node("analyze", analyze_fn)
sub.add_edge(START, "analyze")
sub.add_edge("analyze", END)

main = StateGraph()
main.add_subgraph("code_review", sub.compile(),
    input_map={"code": "code", "task": "task"},
    output_map={"review_result": "result"})
main.add_edge(START, "code_review")
main.add_edge("code_review", END)
```

### 改动文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `agentflow/graph.py` | `StateGraph.add_subgraph()` + `CompiledGraph` 支持子图节点 | ~100 行 |
| `agentflow/__init__.py` | 无新导出 | |
| `demo.py` | 场景 9：子图 demo | ~30 行 |
| `test/test_subgraph.py` | 新增 | ~100 行 |

### 关键设计

| 决策点 | 方案 | 理由 |
|--------|------|------|
| 子图 checkpointer | 默认共享父 checkpointer（同一 thread_id + 子图前缀） | 事件可追溯 |
| 子图 max_steps | 默认继承父，可覆盖 | 防止子图死循环 |
| 子图中断 | 子图内的 interrupt 冒泡到父图 | 人在回路可穿透 |
| input_map | `{"parent_key": "child_key"}` | 明确数据流 |
| output_map | `{"child_key": "parent_key"}` | 同上 |

### 测试用例

1. 子图独立运行并返回结果到父 state
2. 子图内中断 → 父图收到 interrupt
3. 子图 max_steps 超限 → 父图收到 failed
4. 子图 state 与父 state 隔离（input_map/output_map 控制数据流）
5. 嵌套 2 层子图

---

## P2-4：MCP 工具适配（权重 0）

### 问题

当前 ToolRuntime 的工具是内置的（read_file/write_file/run_cmd 等）。MCP（Model Context Protocol）是 Anthropic 提出的外部工具标准协议。预留接口即可，不实现完整 MCP 客户端。

### 方案

在 `agentflow/tools.py` 中新增 `MCPToolProvider` 抽象基类：

```python
class MCPToolProvider:
    """MCP 工具提供者抽象。子类实现具体协议（stdio/HTTP）。"""
    def list_tools(self) -> List[Dict]: ...
    def call_tool(self, name: str, arguments: Dict) -> Any: ...
```

`ToolRuntime` 新增 `register_mcp(provider)` 方法，把 MCP 工具注入到 `run_cmd` 类似的调度层。

### 改动文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `agentflow/tools.py` | `MCPToolProvider` ABC + `ToolRuntime.register_mcp()` | ~30 行 |
| `agentflow/__init__.py` | 导出 `MCPToolProvider` | +1 行 |

### 测试用例

1. Mock MCPToolProvider 注册 → `list_tools()` 可调用
2. Mock MCPToolProvider 工具调用 → 结果返回

---

## 分支策略

4 个任务独立分支，并行开发：

| 任务 | 分支名 | 周期 |
|------|--------|------|
| P2-1 | `feat/p2-1-send-worker` | 2-3 天 |
| P2-2 | `feat/p2-2-join-barrier` | 0.5-1 天 |
| P2-3 | `feat/p2-3-subgraph` | 1-2 天 |
| P2-4 | `feat/p2-4-mcp` | 0.5 天 |

P2-2 的严格 barrier 已并入 P2-1 分支 `codex/p2-send-worker`，后续不再单独开 `feat/p2-2-join-barrier`，除非 CR 要求拆分。

---

## 协作流程

与 P1 一致：每个分支 **Dev Agent → CR Agent → PM merge**，CR 不可跳过。

---

## 验收标准（全部合并后）

1. 现有测试全过
2. 新增测试套件全过：`test_send.py`、`test_subgraph.py`
3. `test_graph.py` 新增 barrier 测试
4. demo.py 8 场景全过（新增 8: 动态扇出；场景 9 留给 P2-3 子图）
5. Python 3.7 下全过

## 当前状态（2026-06-20）

- PM 计划已收敛：P2-1 本轮包含动态 Send/worker、严格 barrier、JSON graph_config 支持、README/demo 示例。
- Dev 分支：`codex/p2-send-worker`。
- Dev 提交：`da67cee feat: add dynamic Send worker execution`。
- Dev 自测：`pytest test/ -q` 154 passed；`verify_py37.sh` 在 Python 3.7.17 下通过；`demo.py` 8 个场景通过。
- 当前阶段：**待独立 CR**。PM 不得合并，直到 CR 产出 `docs/review-notes.md` 并明确 PASS。
