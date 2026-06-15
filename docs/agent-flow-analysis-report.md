# agent-flow 功能分析报告

> 目标：从**功能视角**评估当前 `agentflow` 工程已具备什么能力、还缺什么能力、下一步应该优先做什么。  
> 结论：当前工程已具备一个**可运行的研发型 DAG 工作流内核**，但要进入真正可用的“研发 Agent 编排平台”，还需要补齐 **副作用缓存、真实工具层、结构化任务与动态子图**。

---

## 1. 当前功能定位

当前工程不是一个纯实验 demo，而是已经落地了以下能力：

- **DAG 编排**
  - 节点 = Python 函数
  - 边 = 静态依赖
  - 条件边 = 动态路由
  - 支持回环
- **超步执行**
  - 同一层节点并行执行
  - 顺序合并 state
- **持久化与恢复**
  - SQLite checkpoint
  - 事件日志
  - 中断后恢复
- **人在回路**
  - `interrupt()` / `Command(resume=...)`
- **节点级重试**
- **每节点 LLM 配置**
  - `planner / coder / debugger / reviewer` 已接入 `LLMRegistry`
  - 支持 `mock / anthropic / openai`

这意味着当前项目已经能覆盖研发流程中的“需求 → 分解 → 开发 → 测试 → 评审”骨架。

---

## 2. 功能分层分析

### 2.1 编排层

已具备：

- `StateGraph.add_node`
- `StateGraph.add_edge`
- `StateGraph.add_conditional_edges`
- super-step 并行调度
- 回环
- 最大步数保护

对应文件：

- `agentflow/graph.py`

评价：

- 适合研发流程这种阶段清晰的场景
- 能表达“测试失败回到 coder”、“评审不通过回到 coder”这类典型控制流

不足：

- 还没有显式的 join / barrier 语义
- 条件边目标校验还偏弱
- 动态扇出目前只是“返回节点名列表”，还不是带独立输入的 worker

---

### 2.2 状态层

已具备：

- `StateSchema`
- reducer 合并
- list 追加语义

对应文件：

- `agentflow/state.py`

评价：

- 已经满足多节点汇聚的基础需求
- 适合日志、产物列表、测试报告等累积型字段

不足：

- 目前 state 仍是通用 dict
- 缺少结构化任务模型
- 缺少强类型约束与 schema 校验

---

### 2.3 持久化层

已具备：

- checkpoint 快照
- 事件日志
- 恢复运行
- 时间旅行查看历史

对应文件：

- `agentflow/checkpoint.py`

评价：

- 已经能支撑工作流断点恢复
- 对 demo 级研发任务足够实用

不足：

- 还没有 activity 级缓存
- 一旦节点内先做了 LLM 调用再 interrupt，恢复时可能重复调用
- 当前恢复粒度仍偏 super-step

这是当前最重要的功能缺口。

---

### 2.4 人在回路

已具备：

- `ctx.interrupt(payload)`
- `Command(resume=...)`
- review 节点人工审批

对应文件：

- `agentflow/interrupt.py`
- `agentflow/graph.py`
- `agentflow/nodes.py`

评价：

- 适合需求确认、代码评审、测试结果确认等高风险节点
- 设计方向正确

不足：

- 中断点前的昂贵副作用没有缓存
- 人工审批和 AI 评审还没有分层成独立节点

---

### 2.5 LLM 接入层

已具备：

- `NodeLLMConfig`
- `LLMRegistry`
- 节点级 provider/model/system 配置
- mock 兜底

对应文件：

- `agentflow/llm.py`
- `llm_config.example.json`

评价：

- 已经从“纯 mock 节点”进化到“可切真实模型”
- 每个节点可以单独选模型，适合 planner/coder/debugger 分工

不足：

- LLM 调用还没有统一的 activity 缓存
- 当前只是“内容生成层”，还未变成“可追踪副作用层”

---

### 2.6 研发节点层

已具备：

- `planner`
- `coder`
- `debugger`
- `reviewer`

对应文件：

- `agentflow/nodes.py`

评价：

- 研发流程骨架已经完整
- demo 已能演示：
  - 需求拆分
  - 代码版本迭代
  - 测试失败回环
  - 人工评审
  - LLM provider 配置

不足：

- 这些节点还不是“真实研发节点”
- coder 还没直接操作文件系统
- debugger 还没跑真实测试命令
- reviewer 还没接 diff/变更上下文

---

## 3. 现在已经能做什么

当前工程已经适合做以下事情：

1. **演示研发工作流**
   - 需求 → 分解 → 开发 → 测试 → 评审

2. **验证工作流控制流**
   - 回环
   - 中断
   - 恢复
   - 重试

3. **验证 LLM 节点分工**
   - planner / coder / debugger / reviewer 可使用不同模型

4. **作为后续研发 Agent 平台的底座**
   - 可以继续叠加工具调用、真实代码修改、测试执行和多 Agent 协作

---

## 4. 现在还不能做什么

当前工程还不能算完整的“研发 Agent 平台”，因为还缺：

- **真实文件操作**
  - 读文件
  - 写文件
  - patch 应用
  - diff 生成
- **真实测试执行**
  - shell / pytest / lint
- **副作用缓存**
  - LLM 调用
  - 工具调用
  - activity 结果复用
- **动态 worker**
  - 带独立输入的扇出
- **结构化任务系统**
  - 任务依赖
  - 任务状态
  - 任务验收标准
- **更强的可观测性**
  - 节点耗时
  - token 统计
  - 工具调用轨迹

---

## 5. 功能优先级建议

### P0：先补“可靠性”

1. **Activity / LLM 调用缓存**
   - 避免 interrupt / 恢复重复调用
   - 这是当前最关键的功能缺口

2. **工具调用持久化**
   - 先保证副作用可追踪

3. **图校验增强**
   - 非法节点、非法路由尽早报错

---

### P1：再补“真实研发能力”

4. **ToolRuntime**
   - 文件读写
   - shell
   - patch
   - git diff

5. **结构化 Planner**
   - 输出 tasks / acceptance criteria / clarifying questions

6. **真实 Coder**
   - 从“输出文本”升级为“修改文件”

7. **真实 Debugger**
   - 执行测试命令
   - 收集失败信息

8. **Review 分层**
   - AI review
   - 人工审批

---

### P2：最后补“扩展编排能力”

9. **动态 Send / worker**
10. **join / barrier**
11. **子图**
12. **MCP 工具适配**

---

## 6. 结论

从功能角度看，当前工程已经完成了最重要的一步：

> 把“研发 Agent 编排”从概念，做成了一个可运行、可恢复、可回环、可评审的 DAG 工作流内核。

下一阶段的重点不应再是“再做一个 demo”，而应是：

1. 让 LLM 和工具调用变成**可缓存副作用**
2. 让 coder/debugger 进入**真实代码仓库**
3. 让 planner 输出**结构化任务**
4. 让 workflow 从“演示级”升级为“可交付的研发执行平台”

