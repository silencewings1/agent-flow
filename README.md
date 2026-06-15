# agentflow —— 最小 DAG 编排内核

一个零三方依赖、纯标准库的 Python 实现，把[调研报告](AI-Agent编排Workflow调研报告.md)的核心结论落成可运行代码：

> **「AgentMesh 式角色划分（Planner/Coder/Debugger/Reviewer） × LangGraph 式 DAG 编排 + checkpointer」** —— 报告指出这是目前少有成熟产品占据的结合点。

## 设计映射（报告结论 → 代码）

| 报告结论 | 本实现 |
|---|---|
| 节点=函数 / 边 / 条件边（支持循环） | [graph.py](agentflow/graph.py) `StateGraph.add_node / add_edge / add_conditional_edges` |
| 超步（super-step）执行，同层节点并行 | `CompiledGraph._run_loop` + `ThreadPoolExecutor` |
| 编排者-工作者动态扇出 | 条件边 router 可返回**节点名列表**（见 demo 场景 2） |
| 状态持久化 + 事件溯源 | [checkpoint.py](agentflow/checkpoint.py) 每个 super-step 一份 checkpoint + 追加式事件日志 |
| **恢复时不重跑已完成节点**（硬约束） | frontier 只存「未跑节点」；恢复从 checkpoint 续跑，已完成 state 直接复用 |
| 人在回路（interrupt） | [interrupt.py](agentflow/interrupt.py) `ctx.interrupt(payload)` → 暂停存盘；`Command(resume=...)` 注入恢复 |
| 错误恢复与重试 | `add_node(..., retries=N)` 节点级重试 |
| 时间旅行 | `Checkpointer.history()` / `.events()` |

## 目录结构

```
agentflow/
  state.py       # 共享 state + reducer 合并（overwrite / append）
  interrupt.py   # Interrupt 异常 + Command + interrupt() 原语
  checkpoint.py  # 事件溯源式 SQLite checkpointer
  graph.py       # StateGraph 定义 + CompiledGraph 超步执行器
  nodes.py       # AgentMesh 四节点（mock）+ 条件路由函数
demo.py            # 4 个可运行场景
test_invariants.py # 核心不变量测试（不重跑 / 回环终止）
```

## 快速开始

```bash
python3 demo.py            # 跑 4 个演示场景
python3 test_invariants.py # 跑核心不变量测试
```

无需安装任何依赖（Python 3.8+ 标准库即可）。

## 流水线拓扑（demo 场景 1）

```
START → planner → coder → debugger ──(测试通过)──→ reviewer ──(合并)──→ END
                    ↑           │                      │
                    └─(测试失败)─┘          (打回)───────┘
```

- `debugger` 用条件边：测试失败回退 `coder`（**回环**），通过则进 `reviewer`；
- `reviewer` 用 `interrupt` 请求人工评审 → 暂停存盘 → `Command(resume={"approve": ...})` 恢复；
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

## 接入真实 LLM：每节点独立配置（Claude / OpenAI）

接入点做成**配置文件**（JSON），每个节点可单独指定 provider、模型与参数。同时支持 **Anthropic (Claude)** 与 **OpenAI**，未配置的节点退化为 mock，demo 无需任何 key 即可离线运行。实现见 [llm.py](agentflow/llm.py)，零三方依赖（标准库 `urllib` 直连两家 HTTP API）。

### 1) 写配置文件

复制 [llm_config.example.json](llm_config.example.json) 为 `llm_config.json`：

```json
{
  "defaults": { "temperature": 0.3, "max_tokens": 2048 },
  "nodes": {
    "planner":  { "provider": "anthropic", "model": "claude-opus-4-8",
                  "system": "你是资深需求分析师，把需求拆成可执行子任务" },
    "coder":    { "provider": "openai",    "model": "gpt-4o" },
    "debugger": { "provider": "anthropic", "model": "claude-sonnet-4-6" },
    "reviewer": { "provider": "mock" }
  }
}
```

每节点最终配置 = `provider 默认值 ← defaults ← nodes[name]`（后者优先）。下划线开头的键（如 `_comment`）会被忽略，可用作注释。

### 2) 设置 API Key（环境变量，不落配置文件）

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
```

变量名可在配置里用 `api_key_env` 覆盖。Key 只从环境读取，不写进 JSON、不回显到错误信息。

### 3) 在节点里调用

```python
from agentflow.llm import LLMRegistry
from agentflow import nodes as N

reg = LLMRegistry.load("llm_config.json")  # 文件不存在则全 mock
N.set_registry(reg)                        # 流水线节点据此调用对应 provider
```

节点内通过 `ctx`/registry 拿到 `reg.complete("coder", prompt)`，由配置决定打到 Claude 还是 OpenAI。**图结构、checkpointer、HITL 全部不变。** 见 demo 场景 5（按节点解析 provider/model）。

## 已验证能力

`test_invariants.py` 断言：
- **不重跑**：中断恢复后，中断点之前的节点调用次数不增加（`a=1, b=1` 保持不变）；
- **回环终止**：纯回环图被 `max_steps` 兜底为 `failed`，不会无限跑。

## 边界与可扩展点

- **并发模型**：用线程池（适合 I/O 密集的 LLM 调用）；CPU 密集需换进程池。
- **确定性**：同一 super-step 内的 update 按 batch 顺序合并，保证可复现。
- **持久化后端**：当前为 SQLite；接口很薄，可替换为 Redis/Postgres。
- **未实现（留作扩展）**：分布式 worker、子图嵌套、MCP 工具接入、A2A 跨 Agent 委派（见报告附录 A 与第五章）。
