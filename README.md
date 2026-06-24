# agentflow —— 最小 DAG 编排内核

一个使用 openai/anthropic SDK 接入 LLM、支持 Python 3.12+ 的实现，把[调研报告](docs/agent-flow-research-report.md)的核心结论落成可运行代码：

> **「AgentMesh 式角色划分（Planner/Coder/Debugger/AI Review/Human Review） × LangGraph 式 DAG 编排 + checkpointer」** —— 报告指出这是目前少有成熟产品占据的结合点。

## 设计映射（报告结论 → 代码）

| 报告结论 | 本实现 |
|---|---|
| 节点=函数 / 边 / 条件边（支持循环） | [graph.py](agentflow/graph.py) `StateGraph.add_node / add_edge / add_conditional_edges` |
| 超步（super-step）执行，同层节点并行 | `CompiledGraph._run_loop` + `ThreadPoolExecutor` |
| 编排者-工作者动态扇出 | 条件边 router 可返回 `Send("worker", arg)` 列表（见 demo 场景 8） |
| 状态持久化 + 事件溯源 | [checkpoint.py](agentflow/checkpoint.py) 每个 super-step 一份 checkpoint + 追加式事件日志 |
| **恢复时不重跑已完成节点**（硬约束） | frontier 只存「未跑节点」；恢复从 checkpoint 续跑，已完成 state 直接复用 |
| 人在回路（interrupt） | [interrupt.py](agentflow/interrupt.py) `ctx.interrupt(payload)` → 暂停存盘；`Command(resume=...)` 注入恢复 |
| 错误恢复与重试 | `add_node(..., retries=N)` 节点级重试 |
| 时间旅行 | `Checkpointer.history()` / `.events()` |
| **Activity 缓存（P0-1）** | `ctx.activity(key, fn)` — 以 `(thread_id, node, step, key)` 为键，中断恢复不重跑 LLM |
| **工具调用持久化（P0-2）** | `Checkpointer.tool_calls()` / `.tool_call_summary()` — 自动记录每次 activity 执行 |
| **图校验 + Mermaid（P0-3）** | `StateGraph.validate()` + `.to_mermaid()` — 编译前静态校验 + 可视化导出 |

## 目录结构

```
agentflow/
  state.py       # 共享 state + reducer 合并（overwrite / append / fanout）
  interrupt.py   # Interrupt 异常 + Command + interrupt() 原语
  checkpoint.py  # 事件溯源式 SQLite checkpointer（含 activity 缓存 + tool_calls 表）
  graph.py       # StateGraph 定义（含 validate + to_mermaid）+ CompiledGraph 超步执行器
  graph_config.py # 从 JSON 配置构建 StateGraph / CompiledGraph
  llm.py         # 每节点 LLM 配置层（mock / anthropic / openai/chat / openai/response）
  nodes.py       # planner/coder/debugger/ai_review/human_review + 条件路由函数
  plan.py        # 规划结构与兼容转换
  tools.py       # 受控命令执行工具
conf/
  llm_config.example.json   # 每节点 LLM 配置示例
  graph_config.example.json # pipeline / parallel / retry / timetravel / real_coder / real_debugger / dynamic_send 图配置
docs/
  agent-flow-research-report.md  # 调研报告
  agent-flow-analysis-report.md  # 功能分析报告
test/
  test_invariants.py   # 核心不变量测试（不重跑 / 回环终止）
  test_activity.py     # Activity 缓存 + 工具调用持久化测试
  test_send.py         # 动态 Send/worker + barrier 测试
  test_graph.py        # 图校验 + Mermaid 导出测试
  test_graph_config.py # JSON 图配置测试
  test_planner.py / test_coder.py / test_debugger.py / test_review.py
  test_tools.py / test_py37_compat.py
demo/                # 按功能拆分的 8 个可运行 demo 场景
```

## 快速开始

```bash
source ~/.py_ai/bin/activate

python -m demo                                      # 跑 8 个演示场景
PYTHONPATH=. python -m pytest test/ -q              # 跑全部测试
./scripts/verify_py314.sh                           # Python 3.14 现代语法全量验证
```

项目依赖 openai/anthropic SDK 接入 LLM，核心编排代码使用 Python 3.12+ 现代语法（match/case、PEP 604 `X | Y`、PEP 585 内建泛型、walrus `:=`、f-string 调试语法）。

## 流水线拓扑（demo 场景 1）

```
START → planner → coder → debugger ──(测试通过)──→ ai_review → human_review ──(批准)──→ END
                    ↑           │                                      │
                    └─(测试失败)─┘                         (打回)───────┘
```

- `debugger` 用条件边：测试失败回退 `coder`（**回环**），通过则进 `ai_review`；
- `ai_review` 只产出机器评审意见，不触发中断；
- `human_review` 用 `interrupt` 请求人工评审 → 暂停存盘 → `Command(resume={"approve": ...})` 恢复；
- 打回也回退 `coder`，合并则到 `END`。

## 最小用法

```python
from agentflow import StateGraph, StateSchema, Checkpointer, Command, START, END, append_reducer

schema = StateSchema(reducers={"log": append_reducer})   # log 用追加语义
g = StateGraph(schema)

def planner(state, ctx):
    return {"tasks": ["t1", "t2"], "log": ["planned"]}

def review(state, ctx):
    decision = ctx.interrupt({"ask": "approve?"})         # 暂停等人工
    return {"approved": decision["approve"]}

g.add_node("planner", planner)
g.add_node("review", review)
g.add_edge(START, "planner")
g.add_edge("planner", "review")
g.add_conditional_edges("review", lambda s: END if s["approved"] else "planner")

app = g.compile(Checkpointer("wf.db"))                     # 传文件路径=跨进程持久化
r = app.invoke({"requirement": "..."}, thread_id="job-1")  # → interrupted
r = app.invoke({}, thread_id="job-1", command=Command(resume={"approve": True}))  # → completed
```

## 动态 Send/worker（P2-1）

条件边 router 可以返回 `Send`，在下一个 super-step 动态生成多个同名 worker 实例。每个实例读取自己的 `arg`，但 `arg` 不自动写回全局 state；worker 返回值仍通过 reducer 合并：

```python
from agentflow import Send, StateGraph, StateSchema, START, fanout_reducer

schema = StateSchema(reducers={"results": fanout_reducer})
g = StateGraph(schema)

def split(state, ctx):
    return {"tasks": [{"id": "a"}, {"id": "b"}]}

def route_tasks(state):
    return [Send("worker", {"task": t}, key=t["id"]) for t in state["tasks"]]

def worker(state, ctx):
    return {"results": {ctx.instance_id: state["task"]["id"]}}

g.add_node("split", split)
g.add_node("worker", worker)
g.add_edge(START, "split")
g.add_conditional_edges("split", route_tasks)
```

- `ctx.instance_id` 自动区分同一节点的多个 Send 实例，activity/tool 缓存不会互相撞。
- `fanout_reducer` 合并 `{instance_id: payload}` 字典，适合汇总动态 worker 产出。
- `add_edge(["a", "b"], "join")` 表示严格 barrier，只有所有来源都完成后才调度 `join`。

## P0 新增能力（可靠性增强）

### Activity 缓存（P0-1）—— 中断恢复不重跑 LLM

节点内任何 LLM 调用或耗时操作，只需包一层 `ctx.activity(key, fn)`，中断恢复后直接返回缓存结果，不重复执行：

```python
def planner(state, ctx):
    # 首次执行：调用 LLM → 写入 activity_results 表 → 返回结果
    # 中断恢复：从 activity_results 读取 → 直接返回缓存
    plan = ctx.activity("llm_complete", lambda: get_registry().complete("planner", prompt))
    return {"plan": plan}
```

- **缓存键**：`(thread_id, node, step, activity_key)`；Send worker 会把 `ctx.instance_id` 自动并入 `activity_key`，同名 worker 多实例不互相命中
- **异常缓存**：`fn()` 抛异常也会被记录，重入时**重抛**（避免反复重试同一个会失败的调用）
- **无 checkpointer 时**：退化为直接调用 `fn()`

### 工具调用持久化（P0-2）—— 自动审计层

每次 `ctx.activity()` 执行时**自动写入** `tool_calls` 表（缓存命中时不写），无需节点代码改动：

```python
# 跑完工作流后，查询所有工具调用记录
records = checkpointer.tool_calls(thread_id)
# 返回示例：
# [{"seq": 0, "node": "planner", "tool_name": "llm_complete",
#   "duration_ms": 123.45, "status": "success", ...}, ...]

# 按节点聚合统计
summary = checkpointer.tool_call_summary(thread_id)
# 返回示例：
# [{"node": "planner", "calls": 1, "total_duration_ms": 1234.56,
#   "avg_duration_ms": 1234.56, "successes": 1, "failures": 0}, ...]
```

每条记录含：`thread_id/seq/node/step/tool_name/activity_key/input_summary/output_summary/duration_ms/status/ts`。

### 图校验 + Mermaid 导出（P0-3）—— 编译前静态检查

运行前调用 `validate()` 做拓扑检查，及早发现非法结构：

```python
issues = g.validate()
# 返回 ValidationIssue 列表，每个 issue 有 level:
#   error   → 必须修复（如不可达节点、引用未定义节点）
#   warning → 可疑但合法（如死胡同、条件边可能返回未定义节点）
#   info    → 仅提示（如检测到循环）

# 导出 Mermaid 可视化
print(g.to_mermaid())
# graph TD
#     __start__([__start__]):::startNode
#     planner["planner"]
#     coder["coder"]
#     __end__([__end__]):::endNode
#     __start__ --> planner
#     planner --> coder
```

## JSON 图配置

图结构可从 JSON 声明式配置构建，示例见 [conf/graph_config.example.json](conf/graph_config.example.json)。入口 API：

```python
from agentflow import Checkpointer, build_graph_from_config, load_graph_config
from agentflow import nodes as N

config = load_graph_config("conf/graph_config.example.json")
app = build_graph_from_config(
    config,
    "pipeline",
    node_registry={
        "planner": N.planner,
        "coder": N.coder,
        "debugger": N.debugger,
        "ai_review": N.ai_review,
        "human_review": N.human_review,
    },
    router_registry={
        "route_after_debug": N.route_after_debug,
        "route_after_human_review": N.route_after_human_review,
    },
    checkpointer=Checkpointer("wf.db"),
)
```

- 顶层字段是 `graphs`；每个 graph 支持 `max_steps`、`reducers`、`nodes`、`edges`、`conditional_edges`。
- `reducers` 当前支持 `"append"` / `"fanout"` / `"overwrite"`；未声明的 state key 默认覆盖。
- `edges` / router 返回值里的 `"START"`、`"END"` 会映射到内置 `START` / `END` sentinel。
- `edges` 支持多源 barrier 对象：`{"from": ["w1", "w2"], "to": "join"}`。
- `node` 和 `router` 只从调用方显式传入的 registry 白名单解析；JSON 不允许任意 `import` / `eval`。
- 示例配置统一采用对象映射 + `fn` 的规范写法：`"nodes": {"planner": {"fn": "planner"}, "coder": {"fn": "dummy_coder_fix_test"}}`；带重试的节点写作 `"flaky": {"fn": "flaky", "retries": 2}`。
- 配置校验失败会抛 `ValueError`，错误信息包含 graph 名、字段名或节点名，便于定位 JSON。
- 入口 API 是 `load_graph_config()` + `build_graph_from_config()`；如需先做静态检查，也可先取 `build_state_graph_from_config()` 再 `validate()` / `to_mermaid()`。

## 接入真实 LLM：每节点独立配置

LLM 接入全部通过**配置文件**（JSON），每个节点可单独指定 provider、模型与参数。所有厂商均通过配置文件的 `providers` 字段声明，代码中不硬编码任何厂商。未配置的节点退化为 mock，demo 无需任何 key 即可离线运行。实现见 [llm.py](agentflow/llm.py)，使用 openai / anthropic 官方 SDK 接入。

### 配置文件结构

```json
{
  "providers": {
    "anthropic": {
      "base_url": "https://api.anthropic.com",
      "api_key_env": "ANTHROPIC_API_KEY",
      "model": "claude-sonnet-4-20250514",
      "protocol": "anthropic"
    },
    "openai_chat": {
      "base_url": "https://api.openai.com/v1",
      "api_key_env": "OPENAI_API_KEY",
      "model": "gpt-4o",
      "protocol": "openai/chat"
    },
    "openai_response": {
      "base_url": "https://api.openai.com/v1",
      "api_key_env": "OPENAI_API_KEY",
      "model": "gpt-4o",
      "protocol": "openai/response"
    }
  },
  "defaults": { "provider": "openai_chat", "temperature": 0.3, "max_tokens": 2048 },
  "nodes": {
    "planner":  { "model": "claude-sonnet-4-20250514", "system": "你是资深需求分析师" },
    "coder":    { "model": "gpt-4o", "system": "你是高级工程师，只输出代码" },
    "debugger": { "model": "gpt-4o" },
    "reviewer": { "provider": "mock" }
  }
}
```

- **`providers`**：声明项目支持哪些厂商。`protocol` 字段决定 SDK——`"anthropic"`（Claude Messages API）、`"openai/chat"`（OpenAI Chat Completions，兼容第三方 OpenAI-compatible 服务）、`"openai/response"`（OpenAI Responses API）。
- **`defaults`**：所有节点的默认配置。
- **`nodes`**：每个节点的独立配置，优先级 `provider 协议默认 ← defaults ← nodes[name]`。
- 当前 `ai_review` 节点使用 `reviewer` 这一路 LLM 配置名；它不是图拓扑里的单一 review 节点，人工评审仍由 `human_review` 负责。
- 下划线开头的键（如 `_comment`）会被忽略，可用作注释。

### 在节点里调用

```python
from agentflow.llm import LLMRegistry
from agentflow import nodes as N

reg = LLMRegistry.load("llm_config.json")  # 文件不存在则全 mock
N.set_registry(reg)                        # 流水线节点据此调用对应 provider
```

节点内通过 `get_registry().complete("节点名", prompt)` 调用，由配置决定打到哪个厂商。**图结构、checkpointer、HITL 全部不变。** 见 `/Users/ospacer/cpp_test/agent-flow/demo/demo_llm_config.py`。

## 已验证能力

`test/test_invariants.py` 断言核心不变量：
- **不重跑**：中断恢复后，中断点之前的节点调用次数不增加（`a=1, b=1` 保持不变）；
- **回环终止**：纯回环图被 `max_steps` 兜底为 `failed`，不会无限跑。

`test/test_activity.py` 验证 Activity 缓存 + 工具调用：
- **首次调用执行 fn**，结果正确
- **中断恢复缓存命中**，fn 不再执行
- **不同 key 独立缓存**，不同 thread 互不干扰
- **异常被缓存并重抛**，保留原始异常类型
- **复杂类型（dict/list）序列化/反序列化**正确
- **首次调用自动写入 tool_calls**，缓存命中不新增
- **tool_call_summary 按 node 聚合统计**正确

`test/test_graph.py` 验证图校验 + Mermaid：
- **合法图 validate() 无 error**
- **不可达节点 → error**
- **重复边 → warning**
- **循环 → info**（不阻止编译，合法功能）
- **条件边返回未定义节点 → warning**
- **嵌套函数 return 值不误提取**（AST 静态分析优化）
- **BFS 用 deque 而非 list.pop(0)**
- **to_mermaid() 输出包含所有节点和边**

`test/test_graph_config.py` 验证 JSON 图配置：
- **load_graph_config** 正确读取 JSON；
- **build_graph_from_config** 支持 `START` / `END` alias、append/fanout reducer、条件边 router、节点 retry、多源 barrier edge；
- **nodes** 使用对象映射 + `fn` 的规范写法；
- **node/router/reducer** 只能来自显式 registry，未知名称会抛 `ValueError`。

`test/test_send.py` 覆盖动态 Send/worker：
- 同名 worker 多实例、独立 `Send.arg`、activity cache 实例隔离；
- 空 Send、Send 目标缺失、混合 Send + 普通节点名；
- checkpoint resume 后 Send worker instance_id 稳定；
- 严格 barrier 与 `fanout_reducer`。

`test/test_planner.py`、`test/test_coder.py`、`test/test_debugger.py`、`test/test_review.py`、`test/test_tools.py` 覆盖当前节点能力：
- planner 结构化任务拆分与 mock fallback；
- coder 写文件、兼容旧 state["tasks"]；
- debugger 的 pytest 回环与无 workdir 兼容路径；
- `ai_review` / `human_review` 分层，中断只发生在 `human_review`；
- 受控命令执行白名单与超时/失败路径。

`demo/` 覆盖 8 个场景：流水线 HITL、并行扇出、节点重试、时间旅行、每节点 LLM 配置、真实 Coder 写文件、真实 Debugger pytest 回环、动态 Send/worker。

`./scripts/verify_py314.sh` 覆盖 Python 3.14 语法、导入和测试兼容性。

## 边界与可扩展点

- **并发模型**：用线程池（适合 I/O 密集的 LLM 调用）；CPU 密集需换进程池。
- **确定性**：同一 super-step 内的 update 按 batch 顺序合并，保证可复现。
- **持久化后端**：当前为 SQLite；接口很薄，可替换为 Redis/Postgres。
- **未实现（留作扩展）**：分布式 worker、子图嵌套、MCP 工具接入、A2A 跨 Agent 委派（见 `docs/agent-flow-research-report.md` 附录 A 与第五章）。

## 快速上手指南

从零到跑通完整流水线，只需三步：

### 1) 准备配置文件

```bash
cp conf/llm_config.example.json llm_config.json
```

默认配置使用 OpenAI 的 `gpt-4o` 模型。你也可以编辑 `llm_config.json` 自由切换厂商和模型。

### 2) 设置 API Key

```bash
# 设置环境变量（按你使用的 provider）
export OPENAI_API_KEY="sk-..."

# 如果用 Anthropic
export ANTHROPIC_API_KEY="sk-ant-..."
```

API Key 只从环境变量读取，不落磁盘。

### 3) 运行

```bash
# 跑全部演示场景（无 Key 也能跑，LLM 调用自动降级为 mock）
source ~/.py_ai/bin/activate
python -m demo

# 跑所有测试
PYTHONPATH=. python -m pytest test/ -q

# Python 3.14 现代语法全量验证
./scripts/verify_py314.sh
```

**LLM 调用依赖 openai/anthropic SDK**，核心编排代码无三方运行依赖，Python 3.12+ 标准库即可。

### 接入其他 OpenAI 兼容厂商

只需在 `llm_config.json` 的 `providers` 里加一项，按协议设置 `protocol` 字段：

```json
"providers": {
  "my-chat": {
    "base_url": "https://your-api.com/v1",
    "api_key_env": "MY_API_KEY",
    "model": "your-model",
    "protocol": "openai/chat"
  },
  "my-response": {
    "base_url": "https://your-api.com/v1",
    "api_key_env": "MY_API_KEY",
    "model": "your-model",
    "protocol": "openai/response"
  }
}
```

无需改动任何 Python 代码。
