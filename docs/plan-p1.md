# P1 开发计划

> 基于 `docs/agent-flow-analysis-report.md` 第 5 节，P0 已完成"可靠性"（活动缓存 + 工具审计 + 图校验），P1 聚焦**真实研发能力**：把当前"演示级"工作流升级为"可执行代码修改、可跑测试、可结构化输出任务"的研发执行平台。

---

## P1 任务总览

| 编号 | 事项 | 权重 | 目标 | 预计工作量 | 依赖 |
|------|------|------|------|------------|------|
| P1-1 | ToolRuntime | 15 | 文件/Shell/Patch/Git 工具 + 沙箱隔离 | 2-3 天 | 无 |
| P1-2 | 结构化 Planner | 10 | 输出 `Plan` 对象（tasks + 验收标准 + 澄清问题） | 1 天 | 无 |
| P1-5 | Review 分层 | 5 | 拆 AI review 与人工审批为独立节点 | 0.5 天 | 无 |
| P1-3 | 真实 Coder | 10 | 改真实文件（用 ToolRuntime） | 1.5 天 | P1-1 |
| P1-4 | 真实 Debugger | 10 | 跑真实测试，解析失败，驱动回环 | 1.5 天 | P1-1 |

合计权重 50（与报告一致）。

---

## 依赖图与 Wave 划分

```
        ┌── P1-1 ToolRuntime ──────┐
        │                          │
Wave 1: │  ├── P1-2 Planner        │   ──→ Wave 2:
        │  │                       │            ├── P1-3 Coder (P1-1)
        │  └── P1-5 Review 拆分    │            └── P1-4 Debugger (P1-1)
        │                          │
        └──────────────────────────┘
```

**Wave 1** 三个任务无依赖，可并行 3 个 Dev 窗口。  
**Wave 2** 必须在 P1-1 合并后才能开始。

---

## 架构观察（Dev 实现时请遵循）

1. **`ctx.activity(key, fn, input_summary="")` 是天然的工具调用入口** — ToolRuntime 不需要新建缓存/审计基础设施，工具调用直接包成 `ctx.tool("read_file", lambda: rt.read_file(...))` 即可
2. **Checkpointer 已支持 thread 级沙箱** — `Checkpointer(":memory:")` 默认每个进程独立；P1-1 的 `workdir` 用 `tempfile.mkdtemp(prefix=f"af-{thread_id}-")` 即可
3. **LLM 已能返回任意字符串** — P1-2 的结构化输出只需 prompt 引导 + 简单 JSON 解析 + fallback
4. **`_make_output_summary` 能处理 dict/list/str/bytes/tuple/None** — 结构化 Plan 写入 tool_calls 时有合理摘要
5. **`append_reducer` 已在 demo 中用于 `log` 和 `artifacts`** — P1-3 的"产物列表"可复用此模式

---

## P1-1：ToolRuntime（权重 15，基础设施）

### 问题

当前 `nodes.py` 的 coder/debugger 只是"产出文本"和"硬编码 pass/fail"，**不接触真实文件系统、不跑命令、不生成 patch**。这是"演示级"和"可执行"之间的最大鸿沟。

### 方案

新增 `agentflow/tools.py`，提供 `ToolRuntime` 类，内含 5 类工具：

| 工具 | 方法签名 | 用途 |
|------|---------|------|
| 文件 | `read_file(path) → str` | 读文本 |
| 文件 | `write_file(path, content) → {"path", "bytes"}` | 写文件（自动建父目录） |
| 文件 | `list_dir(path) → list[str]` | 列目录 |
| Patch | `apply_patch(path, unified_diff) → {"path", "applied", "hunks"}` | 应用 unified diff，失败抛异常 |
| Shell | `run_cmd(cmd, timeout=60) → {"stdout", "stderr", "exit_code", "duration_ms"}` | 子进程执行 |
| Git | `git_diff(ref1="HEAD", ref2=None) → str` | 生成 diff（无 git 仓库时返回空字符串） |

**每个工具方法**内部走 `_invoke(name, fn)` → `ctx.activity(f"tool:{name}", fn, input_summary=...)`，自动获得：
- 缓存：同一 thread + node + step + 工具名 命中则不重跑
- 审计：tool_calls 表自动记录耗时、状态
- 可中断：通过 `ctx.interrupt()` 可在未来加"危险命令二次确认"

**沙箱隔离**：`ToolRuntime(thread_id, root="/tmp")` 在 `root/af-{thread_id}/` 下操作；`run_cmd` 拒绝包含 `..` 的路径；提供 `cleanup()` 释放临时目录。

### 改动文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `agentflow/tools.py` | 新增 | `ToolRuntime` 类 + 5 类工具方法 + `cleanup()` |
| `agentflow/__init__.py` | +1 行 | 导出 `ToolRuntime` |
| `agentflow/graph.py` | `NodeContext` 新增 `tool()` 方法（~10 行） | `tool(name, fn, **kwargs)` = `activity(f"tool:{name}", fn, input_summary=kwargs)` |
| `test/test_tools.py` | 新增 | 单元测试：读/写/列/Patch/Shell/Git + 缓存验证 + 沙箱拒绝 |

### 关键设计

| 决策点 | 方案 | 理由 |
|--------|------|------|
| 与 activity 关系 | `ctx.tool()` 是 `ctx.activity()` 的薄包装 | 复用现有缓存/审计，零新基础设施 |
| 沙箱粒度 | thread 级（一个 thread 一个 workdir） | 一个 thread 一次完整工作流，互相隔离 |
| 工具失败 | 抛异常，ctx.activity 自动记 status=exception | 与现有 LLM 失败语义一致 |
| run_cmd 安全性 | 黑白名单（先只允许 `pytest` / `python` / `ls` / `cat`） | 防止 LLM 误调 `rm -rf` |
| 缓存键 | `(thread_id, node, step, "tool:" + name)` | 同节点多次调同工具不冲突 |

### 测试用例

1. `read_file` 读存在的文件 → 返回内容
2. `read_file` 读不存在的文件 → 抛 `FileNotFoundError`
3. `write_file` 写到新路径 → 文件存在，内容正确，父目录自动建
4. `write_file` 覆盖已有文件 → 内容替换
5. `apply_patch` 应用有效 unified diff → 成功，返回 hunks 数
6. `apply_patch` 应用无效 diff（hunk 匹配失败） → 抛异常
7. `run_cmd("python -c 'print(1)'")` → 退出码 0，stdout="1\n"
8. `run_cmd("exit 1")` → exit_code=1
9. `run_cmd` 路径含 `..` → 抛 `PermissionError`
10. `run_cmd` 超时 → 抛 `TimeoutError`，子进程被 kill
11. 同一 thread 同 node 同 step 调 `read_file` 两次 → 第二次命中缓存，fs 访问只发生 1 次（用 monkey-patch 计数）
12. `cleanup()` 后 workdir 不存在
13. `git_diff` 在非 git 仓库中 → 返回 ""

### 分支信息

- 分支名：`feat/p1-1-tool-runtime`
- 基准：`master`
- Wave：1

---

## P1-2：结构化 Planner（权重 10，prompt 工程）

### 问题

当前 `planner` 节点返回 `{"tasks": [...], "plan": "..."}`：
- `tasks` 是从需求字符串按逗号 split 出来的（确定性，但粗糙）
- `plan` 是 LLM 输出的"自由文本"（无法被下游 coder 系统化消费）
- 没有**验收标准**和**澄清问题**，coder 不知道"做对的标准"

### 方案

定义 `Plan` 数据类，planner 输出结构化对象：

```python
@dataclass
class Plan:
    summary: str                            # 1-2 句话总结
    tasks: List[Dict]                       # [{"id": "t1", "title": "...", "details": "..."}, ...]
    acceptance_criteria: List[str]          # ["单元测试覆盖率 ≥ 80%", ...]
    clarifying_questions: List[str]         # ["是否需要支持 OAuth?", ...]
    
    def to_dict(self) -> Dict: ...
    @classmethod
    def from_dict(cls, d: Dict) -> "Plan": ...
    def validate(self) -> List[str]: ...     # 返回错误信息，空列表=合法
```

**LLM prompt** 改为：
```
你是资深需求分析师。分析下面的需求并以 JSON 格式输出计划：
{
  "summary": "...",
  "tasks": [{"id": "t1", "title": "...", "details": "..."}],
  "acceptance_criteria": ["..."],
  "clarifying_questions": ["..."]
}
需求：{requirement}
```

**解析策略**：
- 优先尝试 `json.loads(llm_output)`
- 失败则用正则提取 ```json ... ``` 块
- 还失败则用 mock fallback（确定性文本）
- 解析后 `plan.validate()` 必须通过；不通过则 fallback 到 mock

### 改动文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `agentflow/plan.py` | 新增 | `Plan` dataclass + `parse_plan_from_llm(llm_text, requirement)` 解析函数 |
| `agentflow/__init__.py` | +1 行 | 导出 `Plan` |
| `agentflow/nodes.py` | `planner` 重写 | 返回 `{"plan": plan_dict, "tasks": [...task_ids...], "log": [...]}` |
| `test/test_planner.py` | 新增 | 测试 Plan 校验、LLM 解析、fallback |
| `demo.py` | `scenario_pipeline` 适配 | state["plan"] 改为 dict |

### 关键设计

| 决策点 | 方案 | 理由 |
|--------|------|------|
| 字段命名 | `summary` / `tasks` / `acceptance_criteria` / `clarifying_questions` | 与 LangGraph Agent 规划 schema 对齐 |
| tasks 元素 | dict 而非 str | 后续可挂 file/owner/estimate |
| 解析失败 | fallback 到确定性 mock | 永远不抛异常打断 pipeline |
| 兼容旧 state | `state["tasks"]` 继续存在（取自 plan.tasks 的 id 列表） | 避免破坏后续节点 |

### 测试用例

1. `Plan(summary="x", tasks=[...], acceptance_criteria=[], clarifying_questions=[]).validate()` → `[]` 合法
2. 空 tasks → validate 返回错误
3. 合法 JSON 字符串 → `parse_plan_from_llm` 返回正确 Plan
4. 含 ```json ... ``` 代码块的字符串 → 提取块后解析
5. 完全无法解析的字符串 → fallback 生成确定性 Plan（1 个 task = requirement 本身）
6. planner 节点返回的 state["plan"] 是 dict，`state["tasks"]` 是 id 列表
7. mock LLM 下 demo 仍能跑通（fallback 路径）

### 分支信息

- 分支名：`feat/p1-2-structured-planner`
- 基准：`master`
- Wave：1

---

## P1-5：Review 分层（权重 5，refactor）

### 问题

当前 `reviewer` 节点混了"AI 评审"和"人工审批"两件事：
- 先调 LLM 拿 `opinion`（应该叫 ai_review）
- 再 `ctx.interrupt()` 让人工 approve/reject

这两件事**关注点不同**：AI review 输出技术意见（"变量命名不清"、"缺少边界处理"），HITL 输出业务决策（"合并/打回"）。混在一起让 state schema 杂乱，也难以单独跳过 AI 评审。

### 方案

拆为两个节点：

```python
def ai_review(state, ctx) -> Dict:
    """纯 LLM 评审，输出结构化意见。不中断。"""
    comments = ctx.activity("ai_review", lambda: get_registry().complete(...))
    return {"ai_review": comments, "log": [...]}

def human_review(state, ctx) -> Dict:
    """人在回路：基于 ai_review 决定合并/打回。"""
    decision = ctx.interrupt({
        "ask": "请评审并决定是否合并",
        "ai_review": state.get("ai_review"),
        "code_version": state["code_version"],
    })
    ...
```

**图变化**：
```python
# 旧：debugger → reviewer → (END | coder)
# 新：debugger → ai_review → human_review → (END | coder)
```

`state["ai_review"]` 与 `state["approved"]` 分开存储。

### 改动文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `agentflow/nodes.py` | 拆 `reviewer` → `ai_review` + `human_review`，新增 `route_after_human_review` | 路由函数同步改名 |
| `demo.py` | 4 处 `add_node/reviewer/add_conditional_edges` 改新名字 | scenario_pipeline / scenario_timetravel |
| `test/test_invariants.py` | 不动 | 仍能跑（重构不影响不变量） |
| `test/test_review.py` | 新增 | 验证 ai_review 不中断、human_review 中断、ai_review 输出可在 interrupt payload 中读到 |

### 关键设计

| 决策点 | 方案 | 理由 |
|--------|------|------|
| 拆分粒度 | 2 个节点而非 1 个节点+开关 | 关注点分离，未来可单独重试 AI 评审 |
| ai_review 是否可跳过 | 不提供（永远跑） | AI 评审成本低，HITL 决策需要它做参考 |
| 旧 state 字段 | `state["review_note"]` → `state["human_review_decision"]` | 避免与 ai_review 命名冲突 |
| 旧 review_note 兼容性 | 彻底删除，demo 同步改 | 项目尚无外部消费者，无兼容性负担 |

### 测试用例

1. 跑通到 `ai_review` 节点 → 不应中断
2. 跑通到 `human_review` 节点 → 中断，payload 含 `ai_review` 字段
3. 恢复 resume=`{"approve": False}` → 退回 coder
4. 恢复 resume=`{"approve": True}` → 走 END
5. `state["ai_review"]` 在完成后是字符串
6. `state["approved"]` 在完成后是 bool

### 分支信息

- 分支名：`feat/p1-5-review-layering`
- 基准：`master`
- Wave：1

---

## P1-3：真实 Coder（权重 10，依赖 P1-1）

### 问题

当前 `coder` 节点只把 LLM 输出存到 `state["code"]`（一坨文本），**不接触任何文件**。这导致：
- Debugger 拿不到真实代码可测
- 没有任何"产物"在磁盘上
- 演示价值仅限于看 prompt engineering

### 方案

Coder 接收 `state["plan"]["tasks"]`，对每个 task：
1. 调 LLM 生成该 task 对应的代码（`ctx.activity("llm_code", ...)`）
2. 写入 worktree 的对应文件（`ctx.tool("write_file", path, content)`）
3. 把文件路径加入 `state["artifacts"]`

**文件路径规则**：`{workdir}/src/task_{task_id}.py`（task_id 是 plan 中 t1/t2/...）

**示例数据流**（demo scenario 6）：
```
init: {
  "requirement": "实现一个 fibonacci 函数和单元测试",
  "pass_at_version": 1,   # P1-4 真实测试版才需要
}
↓
planner 产出 Plan(tasks=[{id: "t1", title: "fibonacci 实现"}, {id: "t2", title: "单元测试"}])
↓
coder 写：
  {workdir}/src/task_t1.py   → def fib(n): ...
  {workdir}/src/task_t2.py   → 单元测试
↓
state["artifacts"] = ["src/task_t1.py", "src/task_t2.py"]
```

**兜底**：mock LLM 时，写入的文件内容是 `f"# {task_title}\n# mock code\n"`（含 task 标题的确定性 stub），保证 demo 不需要 API key 也能跑。

### 改动文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `agentflow/nodes.py` | `coder` 重写 | 遍历 plan.tasks，每个 task 调 LLM + write_file |
| `demo.py` | 新增 `scenario_real_coder()` | 场景 6：planner → coder，看 workdir 实际文件 |
| `test/test_coder.py` | 新增 | 验证文件实际写入、artifacts 列表正确、mock 模式可用 |

### 关键设计

| 决策点 | 方案 | 理由 |
|--------|------|------|
| workdir 生命周期 | 跟随 thread 生命周期，`Checkpointer.close()` 不主动清理 | 便于人工检查产物；测试用 `tempfile.TemporaryDirectory` |
| 一文件一 task | `{workdir}/src/task_{id}.py` | 简单可预测；后续可换成 monorepo 风格 |
| LLM 失败 | catch 后写 stub 文件（"mock code for ..."） | 避免 coder 抛异常导致 pipeline 中断 |
| 复用 P1-1 工具 | 走 `ctx.tool("write_file", ...)` | 自动审计写入 |
| 兼容旧 demo | scenario 1-5 改用 Plan 结构但仍跑得通 | 不破坏现有 demo 行为 |

### 测试用例

1. coder 跑完后 `{workdir}/src/task_t1.py` 存在，内容非空
2. `state["artifacts"]` 列出所有写入文件
3. 写文件操作在 `tool_calls` 表中有记录
4. mock LLM 模式下，文件内容是确定性 stub
5. plan 为空时 coder 跳过不报错

### 分支信息

- 分支名：`feat/p1-3-real-coder`
- 基准：`master`（必须等 P1-1 合并）
- Wave：2

---

## P1-4：真实 Debugger（权重 10，依赖 P1-1）

### 问题

当前 `debugger` 节点通过 `version >= pass_at_version` 硬编码 pass/fail — 演示了回环，但**没有真实测试**。要变成"研发平台"，debugger 必须真的执行测试并解析结果。

### 方案

Debugger 节点：
1. 调用 `ctx.tool("list_dir", workdir)` 找到所有 `test_*.py` 或 `*_test.py`
2. 调用 `ctx.tool("run_cmd", "python -m pytest {test_files} --tb=short -q", timeout=120)`
3. 解析 exit_code + stdout：
   - `exit_code == 0` → `tests_passed = True`
   - 否则解析 pytest 输出，提取 failed tests
4. 写 `state["test_failures"]` = `[{test_name, error_msg}, ...]`
5. 写 `state["test_report"]` = LLM 总结（基于 pytest 输出的简短诊断）

**回环**：`tests_passed=False` → 退回 coder → coder 看到 `test_failures` 重写对应文件 → 再次 debugger

**兜底**：未找到任何测试文件 → `tests_passed=True`（"无测试 = 默认通过"），但 log 提示"未发现测试"

### 改动文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `agentflow/nodes.py` | `debugger` 重写 | 真实 pytest 调用 + 解析 |
| `agentflow/tools.py` | 不变 | 复用 P1-1 的 run_cmd |
| `demo.py` | 新增 `scenario_real_debugger()` | 场景 7：故意写一个失败测试 → 看 debugger 报告 → coder 修 → 通过 |
| `test/test_debugger.py` | 新增 | 注入失败 case，验证 failure 解析正确、回环触发、修复后通过 |

### 关键设计

| 决策点 | 方案 | 理由 |
|--------|------|------|
| 测试发现 | `**/test_*.py` + `**/*_test.py` | Python 社区惯例 |
| 解析策略 | 正则提取 `FAILED <path>::<name>` + 错误信息 | 不引入 pytest 内部 API |
| 超时 | 120 秒（可配） | 避免无限挂起 |
| 沙箱 | 跑在 workdir 内，cmd 不允许 `..` | P1-1 已保证 |
| LLM 总结 | 仅在 failures 非空时调 | 通过时无意义 |

### 测试用例

1. workdir 无测试文件 → `tests_passed=True`，log 提示"未发现测试"
2. workdir 有测试文件且全部通过 → `tests_passed=True`，exit_code=0
3. workdir 有失败测试 → `tests_passed=False`，`test_failures` 非空
4. `test_failures[0]` 含 `test_name` 和 `error_msg` 字段
5. debugger 在 coder 之后跑：coder 写错文件 → debugger fail → coder 看 failure 改 → debugger 通过（端到端）
6. 整个回环最多跑 `max_steps` 次（不会死循环）

### 分支信息

- 分支名：`feat/p1-4-real-debugger`
- 基准：`master`（必须等 P1-1 合并）
- Wave：2

---

## 分支与协作流程

每个 P1 任务在独立分支上开发，按 **Dev → CR → PM merge** 流程推进（与 P0 一致）。

### 协作步骤（每个分支）

```
1. Dev 基于 master 创建 feature 分支
2. Dev 实现功能 + 测试，自测通过后 commit
3. CR 检出该分支，git diff 看改动，跑测试，产出 docs/review-notes-p1.md
4. Dev 根据 review-notes 修 bug，再次 commit
5. CR 确认修复后标记"审查通过"
6. PM 从 master 执行 merge，验证后删除 feature 分支
```

### Wave 1（3 并行启动）

| 任务 | 分支名 | 依赖 | 周期 |
|------|--------|------|------|
| P1-1 | `feat/p1-1-tool-runtime` | 无 | 2-3 天 |
| P1-2 | `feat/p1-2-structured-planner` | 无 | 1 天 |
| P1-5 | `feat/p1-5-review-layering` | 无 | 0.5 天 |

**建议并行策略**：
- 启动 3 个 Dev 窗口同步开工
- P1-5 最先完成（半天）→ PM 合并 → 释放 Dev 资源
- P1-1 / P1-2 在 P1-5 合并后陆续完成 → PM 合并

### Wave 2（P1-1 合并后启动）

| 任务 | 分支名 | 依赖 | 周期 |
|------|--------|------|------|
| P1-3 | `feat/p1-3-real-coder` | P1-1 已合并 | 1.5 天 |
| P1-4 | `feat/p1-4-real-debugger` | P1-1 已合并 | 1.5 天 |

P1-3 和 P1-4 可并行（互不依赖）。

### 完整时间线（理想情况）

```
Day 1 ─┬─ P1-1 start
       ├─ P1-2 start
       └─ P1-5 start ───┐
                        │ Day 1.5
                        ↓
                    P1-5 merge → master
                        
Day 1-3: P1-1, P1-2 完成
         P1-1 merge → master (阻塞 P1-3/P1-4)
         P1-2 merge → master

Day 4-5 ─┬─ P1-3 start (依赖 P1-1)
         └─ P1-4 start (依赖 P1-1)

Day 6-7: P1-3, P1-4 完成 → merge → P1 收官
```

---

## 验收标准（Wave 1 完成后）

1. `PYTHONPATH=. python3 test/test_invariants.py` 全部通过
2. `PYTHONPATH=. python3 test/test_activity.py` 全部通过
3. `PYTHONPATH=. python3 test/test_graph.py` 全部通过
4. 新增测试套件全部通过：
   - `test/test_tools.py` (P1-1)
   - `test/test_planner.py` (P1-2)
   - `test/test_review.py` (P1-5)
5. `python3 demo.py` 5 个场景全部正常（场景 5 仍可演示每节点 LLM 配置）
6. 手动验证：中断 demo 后恢复，AI 评审 + 人工审批两个节点分别正确触发

## 验收标准（Wave 2 完成后，在 Wave 1 基础上）

7. `PYTHONPATH=. python3 test/test_coder.py` 全部通过
8. `PYTHONPATH=. python3 test/test_debugger.py` 全部通过
9. `python3 demo.py` 7 个场景全部正常（新增场景 6: 真实 coder，场景 7: 真实 debugger 回环）
10. 端到端 demo：故意写一个会失败的测试 → debugger 报错 → coder 看 failure 重写 → debugger 通过 → human_review → END

---

## 关键风险与缓解

| 风险 | 缓解 |
|------|------|
| P1-1 工具调用可能误删文件 | run_cmd 黑名单 + 路径 `..` 拦截 + 仅在 thread 级 workdir 操作 |
| P1-2 LLM 不按 JSON 输出 | 三层 fallback：直接 JSON → 正则提取代码块 → 确定性 mock |
| P1-3/P1-4 依赖 P1-1 延迟 | Wave 1 内 P1-1 优先级最高；P1-2/P1-5 先做完释放窗口 |
| 测试用例可移植性 | 端到端测试用 `tempfile.TemporaryDirectory()`，不依赖固定路径 |
| API key 依赖 | 全部走 mock 兜底，CI 不需要真实 key |
| P1-1+P1-3+P1-4 一起改 nodes.py | Wave 2 两个任务都改 coder/debugger，须协调避免冲突；建议先后顺序：P1-3 先合，P1-4 后合 |

---

## 不在 P1 范围（已划入 P2 备选）

- 动态 Send / worker（带独立输入的扇出）
- join / barrier（动态分支显式汇聚）
- 子图（节点内嵌工作流）
- MCP 工具适配
- Web UI / 时间旅行可视化
- 分布式执行

如果 P1 收官后有进一步需求，再开 P2 plan。
