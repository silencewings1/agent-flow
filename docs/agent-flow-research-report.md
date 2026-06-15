# agent-flow（DAG 图节点管理）调研报告

> 面向项目研发场景：需求分析 / 任务分解 / 代码开发 / 测试
> 报告时点：2026 年初。证据按来源质量分层标注，⭐ = 一级来源（厂商官方文档/官方博客/研究论文），其余为博客/二级来源或单篇预印本。

## 一、核心结论：两条路线的分化（⭐ 高置信）

整个领域已清晰分化为两种架构，且这一二分法被 Anthropic、LangGraph、微软 Durable Task 三方原始文档高度一致地采用：

| | **Workflow（工作流）** | **Agent（自主体）** |
|---|---|---|
| 控制流 | 预定义代码路径编排 LLM 与工具 | LLM 在运行时自行决定流程与工具调用 |
| 特性 | 可预测、一致 | 灵活、自主 |
| 适用 | 任务边界明确 | 问题不确定、需探索 |
| 代价 | — | 以**延迟和成本**换取更好的任务表现 |

微软 Durable Task 进一步把它落成两类可执行形态：
- **确定性工作流**：代码定义控制流（含分支、并行、错误处理），LLM 只是其中一步；
- **Agent 主导工作流 / agent loop**：LLM 驱动控制流，运行时决定工具调用顺序与终止条件。

> 对你的项目的含义：研发流水线（需求→分解→开发→测试）这种**阶段边界清晰**的场景，主干用 workflow/DAG 编排是更稳妥的基线；把"自主"留给单个节点内部（如"开发节点"内允许 Agent 自由探索代码库）。这正是业界主流做法。

来源：[Anthropic — Building Effective Agents](https://anthropic.com/research/building-effective-agents) · [LangGraph — Workflows & Agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents) · [Microsoft Durable Task for AI Agents](https://learn.microsoft.com/en-us/azure/durable-task/sdks/durable-task-for-ai-agents)

---

## 二、DAG / 图编排的代表实现：LangGraph（⭐ 高置信）

LangGraph 是当前 DAG 图编排最有参考价值的实现样板，其节点/边抽象可直接借鉴：

**图编排机制**
- `StateGraph`：把**节点（函数）**用 `add_node` 注册，用 `add_edge` 连接成图；
- **条件分支**用 `add_conditional_edges`——路由函数返回下一个节点名称，实现动态跳转；
- **编排者-工作者（orchestrator-worker）模式**：编排者动态拆解任务、委派子任务、综合输出；用 **Send API** 在子任务无法预先定义时**动态创建带独立状态的 worker 节点**。

这与 Anthropic 描述的 orchestrator-workers 模式一致——它与"静态并行"的关键区别是：**子任务不是预先定义的，而是编排者根据具体输入动态确定的**。Anthropic 明确把"需要跨多文件做复杂改动的编码产品"列为该模式的典型用例——这正好命中研发场景。

来源：[LangGraph — Workflows & Agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents) · [Anthropic — Building Effective Agents](https://anthropic.com/research/building-effective-agents)

> **DAG 静态 vs 动态规划的权衡**：DAG 的强项是可预测、可调试、可并行、易观测；弱项是无法应对"事先不知道要拆成几个子任务"的情况。orchestrator-worker + Send API 是一个折中：**图结构是静态的，但某一层的 worker 数量在运行时动态展开**。建议研发流水线采用这种"静态骨架 + 动态展开"的混合形态，而不是纯静态 DAG 或纯自主 Agent。

---

## 三、状态持久化 / 人在回路 / 错误恢复：统一由 Checkpointer 支撑（⭐ 高置信）

LangGraph 把三个关键工程能力统一在一个 **checkpointer** 机制上——这是值得直接照搬的设计决策：

- **持久化**：每一步图执行都读/写一份图状态 checkpoint（存储 Agent 工作所需的全部状态），提供线程级短期记忆；
- 由此一并启用：**对话连续性、人在回路、时间旅行（time travel）、容错**；
- **人在回路（HITL）**用 `interrupt` 函数实现：暂停图执行 → 将线程标记为 interrupted → 把入参存入持久化层（语义类似 Python 的 `input()`，但面向生产）。被中断的线程**除存储外不占用计算资源**，可在数月后、在另一台机器上通过 `Command(resume=...)` 恢复；
- **错误恢复**：checkpointer 允许在中断或失败后从断点恢复执行。

来源：[LangGraph Persistence 文档](https://docs.langchain.com/oss/javascript/langgraph/persistence) · [LangChain — Human-in-the-loop with interrupt](https://blog.langchain.dev/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt/)

### 持久化执行（Durable Execution）正成为独立基础设施层（⭐ 高置信）

一个重要趋势：**持久化执行作为与 Agent 框架解耦的基础设施层**正在兴起。微软 Durable Task 明确声明"**不是 Agent 框架**"，而是提供状态管理、checkpointing 与分布式协调，可与任意框架（Microsoft Agent Framework、LangChain、AutoGen，或直接调用 LLM API）配合：

- **自动 checkpoint 每一次状态转移**（LLM 响应、工具调用结果、控制流决策）到持久存储；
- 故障时在健康节点上**自动从最近 checkpoint 恢复，且不重复已完成的 LLM 调用**——同时节省 token 花费与墙钟时间。

来源：[Microsoft Durable Task for AI Agents](https://learn.microsoft.com/en-us/azure/durable-task/sdks/durable-task-for-ai-agents)（同类思路亦见 Temporal 生态）

> 对项目的含义：把"编排逻辑"与"持久化/重放基础设施"分层。LLM 调用昂贵且不确定，**故障恢复时绝不能重复已完成的 LLM 调用**是核心要求——这本质上是成熟的事件溯源（event sourcing）/重放模式在 Agent 场景的应用。

---

## 四、面向研发全流程的 Agent 系统：两种形态（⭐ 高置信）

经核实的两个代表，恰好代表两种设计哲学：

**形态 A — 流水线式（AgentMesh）**
四个专职 Agent 直接映射研发流程：
- **Planner** → 需求分析 + 任务分解
- **Coder** → 代码生成
- **Debugger** → 测试 + 纠错
- **Reviewer** → 最终质量评审

值得注意：AgentMesh 当前原型**刻意采用固定顺序编排（fixed sequential orchestration）而非 DAG/图**，并明确把"引入 DAG 编排（引用 MacNet）/ LangGraph"列为**尚未实现的未来方向**。这说明：即便是专门做研发流水线的研究系统，从"固定顺序"升级到"DAG"也仍是公认有价值但未完成的一步——一开始就上 DAG，是走在前面的。

**形态 B — 事件流 + 委派式（OpenHands）**
由三大组件构成：① 以 `step` 函数为核心的 Agent 抽象（输入当前状态、输出下一个动作）；② 按时序记录动作与观察的**事件流**；③ 执行动作产生观察的 **runtime**。
- 用**事件流 + 委派**而非原生 DAG；
- 通用 `CodeActAgent` 在"动作-观察"循环里操作仓库（文件编辑、代码执行）；
- **不存在显式的 需求→分解→编码→测试 流水线**；
- 多 Agent 协作通过 `AgentDelegateAction` 委派子任务实现（如 CodeActAgent 把网页浏览委派给 BrowsingAgent）。

来源：[AgentMesh (arXiv 2507.19902)](https://ar5iv.labs.arxiv.org/html/2507.19902) · [OpenHands (arXiv 2407.16741)](https://arxiv.org/html/2407.16741v3)

> 对项目的含义：本项目目标（需求/分解/开发/测试 + DAG 节点管理）**最接近 AgentMesh 想做但还没做的形态**——即"AgentMesh 的角色划分" × "LangGraph 的 DAG 编排 + checkpointer"。这是一个目前尚未被成熟产品占据的结合点。

---

## 五、互操作协议栈正在形成（⭐ 高置信）

三个协议分层覆盖不同需求，建议在架构中预留这些接口：

| 协议 | 定位 | 机制 |
|---|---|---|
| **MCP**（Model Context Protocol） | 工具调用基础层 | JSON-RPC 客户端-服务器接口，安全工具调用 + 类型化数据交换；连接 LLM Agent 与外部工具/数据源 |
| **A2A**（Agent-to-Agent） | 点对点任务委派 | 基于能力描述的 **Agent Card**，面向企业工作流的安全多 Agent 协作 |
| **ANP**（Agent Network Protocol） | 去中心化发现 | 基于 W3C **DID** + JSON-LD 图，开放网络的 Agent 发现与去中心化协作（最去中心化） |

MCP 由 Anthropic 于 2024-11 提出并持续维护（2025-03、2025-06 修订）。

来源：[A Survey of Agent Interoperability Protocols (arXiv 2505.02279)](https://arxiv.org/abs/2505.02279)

> 对项目的含义：**节点内的工具调用走 MCP**（让节点能复用生态里现成的工具服务器），**跨 Agent 委派预留 A2A 风格的接口**。短期 ANP 可不实现。

---

## 六、未来方向：自适应拓扑编排（⚠️ 中等置信，单篇预印本）

最前沿但证据较弱的方向是**自适应/动态图编排**。论文 AdaptOrch 提出：
- 把任务分解为**带依赖标注的 DAG**；
- 依据结构特征在**四种标准拓扑（并行 / 串行 / 层级 / 混合）间动态路由**；
- 报告相对静态单拓扑基线**提升 12–23%**。

其更深层论点值得关注：**随着 LLM 基准性能趋同，"编排拓扑"（多 Agent 如何被协调/并行/综合的结构组合）对系统级性能的主导作用正在超过单模型能力的选择。**

> ⚠️ 证据等级：单篇未经同行评审的 arXiv 预印本，12–23% 为自报告基准（跨 SWE-bench/GPQA/HotpotQA），未经独立复现。应作为**"论文主张"**而非既定事实引用。

来源：[AdaptOrch (arXiv 2602.16873)](https://arxiv.org/html/2602.16873)

---

## 七、工程建议（基于以上证据综合）

1. **主干用静态 DAG，节点内允许自主**：研发四阶段（需求/分解/开发/测试）做成显式 DAG 节点；单个节点内部（尤其"开发"）允许 Agent 自由调用工具与迭代。这是 workflow×agent 二分法的标准折中。
2. **照搬 LangGraph 的抽象**：`节点=函数`、`边=add_edge`、`条件分支=路由函数返回节点名`、`动态扇出=Send API`。不必从零设计图模型。
3. **持久化与编排分层**：用统一的 checkpointer / durable execution 层承载状态持久化、HITL、错误恢复。**硬约束：故障恢复绝不重复已完成的 LLM 调用。**
4. **HITL 介入点**：在需求评审、代码合并、测试结果确认处用 `interrupt` 式暂停——中断线程不占算力、可长期挂起后恢复。
5. **工具走 MCP，跨 Agent 委派预留 A2A**。
6. **角色划分参考 AgentMesh**（Planner/Coder/Debugger/Reviewer），编排骨架参考 LangGraph——这个结合点目前尚无成熟产品占据。

---

## 八、重要限制与待解问题

**报告的 caveats：**
- 原始问题点名的多数框架/产品（**n8n、Dify、Temporal、Airflow、Coze、Flowise、AutoGen、CrewAI、MetaGPT、ChatDev、Devin、SWE-agent、GitHub Copilot Workspace**）**未获得经三票核实的一级证据**，其各自的 DAG/节点抽象/调度模型需进一步逐一调研。本报告未对它们逐一展开，以免给出未经验证的描述。
- 一条被**否决（0-3）**的声明：某博客称编排平台分为 code-first / config-first / workflow-first 三类——验证未通过，未纳入正文。
- 厂商自述（Durable Task、LangGraph 博客）对"能力存在性"可信，但涉及竞品对比/营销表述需保留判断。

**待解问题：**
1. 上述未核实框架各自的图编排与状态管理机制；
2. DAG 静态编排 vs 动态自主规划在**真实研发任务上的量化权衡**（成功率/成本/延迟/可调试性）缺乏跨框架对照基准；AdaptOrch 的 12–23% 能否独立复现并推广到研发场景；
3. MCP/A2A/ANP 在生产级多 Agent 研发系统中的**实际采用率与安全边界**（委派链中的权限/审计）；
4. 研发全流程中 HITL 的**最佳介入点**与可观测性（trace/span/状态回放）的系统化实践对比。

---

## 附录 A、逐框架对照表（带证据等级）

下表逐一对照原始问题点名的框架/产品。证据等级：⭐ = 一手官方文档/论文且经多源核实；🅑 = 仅产品博客/二手来源（无公开技术细节）；⚪ = 未取得可信一手证据。

### A 组 — 工作流 / 编排引擎（偏 DAG）

| 框架 | 是否原生 DAG | 编排核心 | 节点/任务抽象 | 状态管理与持久化 | 调度/执行模型 | 证据 |
|---|---|---|---|---|---|---|
| **Apache Airflow** | ✅ 严格 DAG（核心原语） | 声明式 DAG，工作流即 DAG | **Task**（由 Operator/Sensor 实例化，均继承 BaseOperator，Task≈Operator） | 必需的元数据库（PostgreSQL/MySQL），存 task/DAG/变量状态；任务间用 XCom 传值 | scheduler 触发并把 Task 提交给 executor（executor 是 scheduler 的配置属性、运行在其进程内）；依赖用 `>>`/`<<` 表达 | ⭐ |
| **n8n** | ◐ 有向图（含显式循环，非严格无环） | 节点 + 有向连接的图 | **Node**：Core（Code/Filter/Merge/Switch/HTTP）、App/Action、Trigger、Cluster（AI/LangChain） | 数据沿连接在节点间流动；执行数据可持久化 | If/Switch 分支、Merge 合并、Loop Over Items 循环；多分支为**确定性顺序执行**（非真并行） | ⭐ |
| **Dify** | ◐ 可视化图编排（支持 iteration/loop，非严格无环） | 连接「单步节点」构图 | **节点**：User Input、Trigger、LLM、Knowledge Retrieval、Code、Conditional Branching、IF/ELSE、HTTP、变量聚合等 | 节点间变量传递 | 两种流程：**Workflow**（从头到尾跑一次，面向自动化/批处理）vs **Chatflow**（每条用户消息触发，面向对话） | ⭐ |
| **Flowise** | ◐ 节点+边画布；Agentflow 官方称「有向循环图 DCG」 | 可视化画布，连接定义路径 | **节点**；三种构建器：Assistant（入门）、Chatflow（单 agent/简单 LLM）、Agentflow（多 agent，前两者超集，基于 LangGraph） | 画布连接定义流向 | Condition 节点分支、Loop 节点（带 Max Loop Count）回跳、Iteration 节点 for-each、路由 | ⭐ |
| **Temporal** | ❌ 刻意不用 DAG | **命令式代码驱动**的持久化执行（Go/Java/TS/Python 写 Workflow） | **Workflow Definition/Type/Execution** + **Activity**（编排与副作用严格分离：API/DB/文件/LLM 调用必须进 Activity） | **事件溯源**：每个 execution 维护追加式 Event History；确定性**重放**重建状态（非内存快照，Activity 结果复用不重算） | worker 持续轮询任务队列；Workflow Task 推进用户代码至阻塞/完成后发命令；强制确定性，Replay 测试失败=非确定性；signal/query（query 不推进工作流） | ⭐ |
| **Coze（扣子）** | ⚪ 据称节点式工作流编排 | （工作流画布，节点拖拽） | （节点） | — | — | ⚪ 未取得可信一手证据 |

### B 组 — 多 Agent 框架

| 框架 | 是否原生 DAG | 编排核心 | 节点/Agent 抽象 | 状态管理与持久化 | 调度/执行模型 | 证据 |
|---|---|---|---|---|---|---|
| **AutoGen**（v0.4+） | ❌ 非 DAG | **event-driven / actor 式消息传递**运行时 | Agent 为独立执行单元，runtime 管理身份与生命周期 | runtime 管理连接/生命周期状态；agent 内部状态自管 | 消息经 runtime 中转 + Topic/Subscription 发布订阅；Standalone（单进程）/Distributed（多进程）运行时；GroupChat/Sequential/Handoffs 等为**上层设计模式**（上层可拼出类 DAG 流程） | ⭐ |
| **CrewAI** | ◐ Flows 构成有向图（支持循环/分支，非严格无环） | 两套：**Process**（Crew 编排）+ **Flows**（事件驱动） | Crew/Agent/**Task**；Flows 中方法为节点、`@listen` 关系为边 | Flows 状态：Unstructured（字典，自动 UUID）/ Structured（Pydantic）；`@persist` 持久化（默认 SQLite，支持恢复/fork） | Process：**Sequential**（线性链）vs **Hierarchical**（manager agent 动态委派，需 manager_llm/agent）；Flows：`@start`/`@listen`/`@router` + `or_`/`and_` 汇聚分支 | ⭐ |
| **MetaGPT** | ❌ 消息驱动（非 DAG） | **发布-订阅 + 共享消息池（Environment）**，编码 SOP | **Role**（按「输入→watch / 输出→Message / 任务→action」设计） | Environment 广播消息；消息以 `cause_by` 为订阅标签路由 | agent 经 `_watch` 订阅、`publish_message` 发布；SOP 由 action 的 cause_by 链条串联；支持循环（B→C→D→B），靠 `env.is_idle` 判终止 | ⭐ |
| **ChatDev** | ◐ 1.0 为线性链；MacNet/2.0 引入 DAG | **Chat Chain**（聊天链）：瀑布式 design→coding→testing→documenting | **Phase**（ChatChain→Phase→Role），phase 内两两 agent 结构化对话（如 CEO/CTO/Programmer） | （阶段间上下文传递） | 1.0 链式（chain-shaped）顺序；MacNet 用 DAG 支持多 agent 协作；2.0 演进为可视化工作流画布 | ⭐ |

### C 组 — 软件研发 Agent 产品

| 产品 | 是否原生 DAG | 编排核心 | 抽象/工具 | 状态/持久化 | 执行模型 | 证据 |
|---|---|---|---|---|---|---|
| **SWE-agent** | ❌ 非 DAG | **ReAct 式 agent loop**（thought-action-observation） | **Agent-Computer Interface (ACI)**：为 LM 设计的浏览/查看/编辑/执行命令与反馈格式 | （上下文/历史；EnIGMA 加 Summarizer 处理长上下文） | LLM 循环；命令(action)↔反馈(observation)迭代；EnIGMA 扩展 Interactive Agent Tools | ⭐ |
| **Devin** | ⚪→推断非 DAG | 自主 agent loop（长程推理与规划，内嵌于推理而非独立 planner） | shell + code editor + browser（沙箱环境） | 「每步回忆相关上下文、随时间学习、修复错误」 | 自主规划-执行循环（可处理数千步决策）；实时汇报、接受反馈协作 | 🅑 仅产品博客，无公开技术报告 |
| **GitHub Copilot Workspace** | ⚪→推断线性 | 任务中心：issue→分析→方案 | 自然语言任务；从 issue/dashboard/repo 多入口启动 | 会话持久、可恢复（session list） | 「提交后开始分析如何求解」；外界已知的 spec→plan→implement 多步结构在该文档中未明确描述 | 🅑 入门文档，未披露内部阶段结构 |

### 已纳入正文的两个对照基准（上一轮已验证）

| 框架 | 是否原生 DAG | 编排核心 | 关键点 | 证据 |
|---|---|---|---|---|
| **LangGraph** | ✅ StateGraph（图，支持条件边与循环） | `add_node`/`add_edge`/`add_conditional_edges` + Send API 动态扇出 worker | checkpointer 统一支撑持久化/HITL(`interrupt`)/时间旅行/容错 | ⭐ |
| **OpenHands** | ❌ 事件流 + 委派 | step 函数 + event stream + runtime | `AgentDelegateAction` 委派；无显式 需求→分解→编码→测试 流水线 | ⭐ |

### 关键差异分析

1. **三种编排范式**，而非简单的「DAG / 非 DAG」二分：
   - **严格 DAG（声明式）**：仅 Airflow。工作流就是一张静态无环图，最可预测、最易调度与观测。
   - **有向图 / 有环图（可视化或代码）**：n8n、Dify、Flowise、CrewAI Flows、LangGraph、ChatDev 2.0/MacNet。保留「节点+边」的图心智模型，但允许循环/条件路由以支持迭代——这是当前 **AI workflow 产品的主流形态**。
   - **非图范式**：Temporal（命令式代码 + 事件溯源重放）、AutoGen（actor 消息传递）、MetaGPT（发布订阅 + SOP）、SWE-agent/Devin（自主 agent loop）。

2. **「图」在两个层面被使用，别混淆**：n8n/Airflow/Dify 的图是**面向最终用户的编排画布**；而 CrewAI Flows、LangGraph 的图是**面向开发者的代码级控制流**。你的研发场景更需要后者（程序化、可版本化、可测试）。

3. **状态持久化分两派**：事件溯源/重放（Temporal、LangGraph checkpointer、CrewAI `@persist`）vs 元数据库快照（Airflow）。对「故障不重复昂贵 LLM 调用」这一硬需求，**事件溯源派更契合**。

4. **多 Agent 框架普遍不用 DAG**：AutoGen（消息）、MetaGPT（订阅+SOP）、CrewAI Process（线性/层级委派）都靠运行时动态决定流程。代价是可预测性与可观测性下降——这正是为什么把它们包在一个静态 DAG 骨架里（如 LangGraph）是更稳的工程选择。

5. **研发 Agent 产品几乎都是 agent loop**：SWE-agent（ReAct+ACI）、Devin、Copilot Workspace 都偏向「LLM 自主规划循环」而非显式流水线图——印证了正文结论：**「AgentMesh 式角色划分 × DAG 骨架」目前仍是少有人占据的结合点**。

### 本对照表的证据缺口

- **Coze**：官方文档源在本轮被判为不可靠，未取得可信声明，对照表中留空。
- **Devin / Copilot Workspace**：均为闭源产品，仅有产品博客/入门文档，无公开技术架构报告，DAG 判定为**推断**而非证实。
- **n8n/Dify/Flowise/CrewAI** 为快速迭代产品，节点类型清单非穷尽、版本间可能变化（如 Flowise Agentflow V1 弃用、V2 为当前版本），引用时建议核对最新官方文档。

---

## 九、已验证来源清单（按质量）

**⭐ 一级来源（primary）**
- [Anthropic — Building Effective Agents](https://anthropic.com/research/building-effective-agents)
- [LangGraph — Workflows & Agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- [LangGraph — Persistence](https://docs.langchain.com/oss/javascript/langgraph/persistence)
- [LangChain — HITL with interrupt](https://blog.langchain.dev/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt/)
- [Microsoft — Durable Task for AI Agents](https://learn.microsoft.com/en-us/azure/durable-task/sdks/durable-task-for-ai-agents)
- [OpenHands (arXiv 2407.16741)](https://arxiv.org/html/2407.16741v3)
- [AgentMesh (arXiv 2507.19902)](https://ar5iv.labs.arxiv.org/html/2507.19902)
- [Agent Interoperability Protocols Survey (arXiv 2505.02279)](https://arxiv.org/abs/2505.02279)
- [AdaptOrch (arXiv 2602.16873)](https://arxiv.org/html/2602.16873)（⚠️ 预印本）

**⭐ 一级来源（逐框架对照表，附录 A）**
- [Apache Airflow — Core Concepts](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/overview.html)
- [n8n Docs — Connections / Node types / Flow logic](https://docs.n8n.io/)
- [Dify Docs — Workflow / Chatflow](https://docs.dify.ai/en/guides/workflow)
- [Flowise Docs — Agentflow v2](https://docs.flowiseai.com/using-flowise/agentflowv2)
- [Temporal Docs — Workflows](https://docs.temporal.io/workflows) · [架构 README](https://github.com/temporalio/temporal/blob/main/docs/architecture/README.md)
- [AutoGen — Architecture](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/core-concepts/architecture.html)
- [CrewAI — Processes](https://docs.crewai.com/concepts/processes) · [Flows](https://docs.crewai.com/concepts/flows)
- [MetaGPT — Agent Communication](https://docs.deepwisdom.ai/main/en/guide/in_depth_guides/agent_communication.html)
- [ChatDev (GitHub)](https://github.com/OpenBMB/ChatDev)
- [SWE-agent — Background / ACI](https://swe-agent.com/latest/background/) · [arXiv 2405.15793](https://arxiv.org/abs/2405.15793)

**🅑 仅产品博客/入门文档（证据等级较低）**
- [Cognition — Introducing Devin](https://www.cognition.ai/blog/introducing-devin/)
- [Copilot Workspace — Getting Started](https://github.com/githubnext/copilot-workspace-user-manual/blob/main/getting-started.md)

**其余博客/二级来源**（用于角度扫描，未作为核心声明依据）：Temporal 系列、futureagi、calmops、digitalapplied、HuggingFace 博客、groovyweb、ZenML、xgrid 等。

---

## 附：调研方法与统计

本报告由 deep-research 工作流生成，流程：分解调研角度 → 并行 web 搜索 → 去重抓取来源 → 提取声明 → 对置信度最高的声明做 3 票对抗式验证（need 2/3 refutes to kill）→ 合并去重、按置信度排序、标注引用。

**第一轮（正文核心发现）**
- 调研角度：5
- 抓取来源：22
- 提取声明：106
- 验证声明：25（**确认 24，否决 1**）
- 综合后核心发现：7

**第二轮（附录 A 逐框架对照表）**
- 调研角度：5
- 抓取来源：27
- 提取声明：118
- 验证声明：25（**确认 25，否决 0**，全部为 A 组框架）
- 补充定向抓取：对 B 组（AutoGen/CrewAI/MetaGPT/ChatDev）与 C 组（SWE-agent/Devin/Copilot Workspace）的已识别一手源做了 9 次定向 WebFetch 补全
- 证据缺口：Coze、Devin、Copilot Workspace 未取得可信一手技术证据（已在表中标注）
