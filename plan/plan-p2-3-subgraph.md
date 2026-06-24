# P2-3 子图开发计划

> 基于 `docs/plan-p2.md` P2-3 节。当前节点是 Python 函数，无法在节点内再编排 DAG。
> 本计划引入 `StateGraph.add_subgraph()`，把一个已编译的 `CompiledGraph` 注册为父图的一个节点。
> 分支：`feat/p2-3-subgraph`，基准 `master`，与当前 `fix/cr-backlog-round1` 互不干扰。

---

## 1. 核心设计：子图 = 一个特殊的 NodeFn wrapper

### 1.1 关键洞察

`CompiledGraph._exec_node`（`agentflow/graph.py:942-975`）已统一走：

```python
try:
    update = node.fn(run_state, ctx)
    return {"kind": "ok", "update": update}
except Interrupt as it:
    return {"kind": "interrupt", "payload": it.payload}
except Exception as exc:
    # retries 兜底，超限转 {"kind": "error", ...}
```

**子图不需要改执行循环**。只要把一个已编译的 `CompiledGraph` 包成一个 NodeFn，就能复用全部并行 / 重试 / checkpoint / interrupt 机制：

- 子图 wrapper 调 `sub.invoke(child_state, thread_id=sub_tid, command=...)` 拿 `RunResult`
- `status="interrupted"` → `raise Interrupt(result.interrupt_payload)` → 父图现有 `except Interrupt` 自然捕获
- `status="failed"` → `raise RuntimeError(result.error)` → 父图现有 `except Exception` 捕获 → 走父图节点级重试
- `status="completed"` → 用 `output_map` 把子图 state 字段映射回父图 partial update

### 1.2 API

```python
StateGraph.add_subgraph(
    name: str,
    subgraph: CompiledGraph,
    input_map: Dict[str, str],     # {parent_state_key: child_state_key}
    output_map: Dict[str, str],    # {child_state_key: parent_state_key}
    retries: int = 0,              # 子图节点级重试（与 add_node 语义一致）
    retry_backoff: float = 0.0,    # 重试间隔秒
) -> StateGraph
```

- **共享父 checkpointer**，独立 thread_id：`f"{ctx.thread_id}::sub::{name}::s{ctx.step}::{ctx.instance_id}"`
  - 含父 step + instance_id → 同一父节点多次重入（如回环）也各自独立子 thread
  - thread_id 稳定 → 子图 resume 时用自己的 checkpoint 续跑，**子图内已完成节点不重跑**（保持硬不变量）
- 子图 max_steps 由子图自己的 StateGraph 决定，与父图独立
- 子图中断时 `interrupt_payload` 直接冒泡给父图；resume 值经 `ctx.resume_value` → `Command(resume=...)` 透传给子图 `invoke`

### 1.3 数据流示例

```
父图:  prepare ──► code_review(子图) ──► END
                   │
                   ├─ input_map: {"code":"code", "task":"task"}
                   └─ output_map: {"review_result":"result"}

子图 code_review 内部:
  analyze ──► human_gate(interrupt) ──► summarize
```

首次 `invoke` → 子图跑到 `human_gate` 中断 → 父图 `status="interrupted"`，payload 含 `ai_review`。
`invoke(command=Command(resume={"approve": True}))` → 子图 gate 恢复、summarize 跑完 → 父图 `status="completed"`，`state["review_result"]` 非空。

---

## 2. wrapper 核心逻辑

```python
def _make_subgraph_fn(spec: _SubgraphSpec) -> NodeFn:
    sub = spec.subgraph

    def subgraph_node(state, ctx):
        # 1) input_map: 父 state → 子 state（仅映射声明的字段）
        child_state = {
            child_key: state[parent_key]
            for parent_key, child_key in spec.input_map.items()
            if parent_key in state
        }
        # 2) 稳定子 thread_id（含父 step + instance_id，回环/Send 场景各自独立）
        sub_tid = f"{ctx.thread_id}::sub::{spec.name}::s{ctx.step}::{ctx.instance_id}"
        # 3) resume 透传：父图 resume_for → ctx.resume_value → Command
        command = (Command(resume=ctx.resume_value)
                   if ctx.resume_value is not _MISSING else None)
        result = sub.invoke(child_state, thread_id=sub_tid, command=command)
        # 4) 子图结果 → 父图 partial update / 异常
        if result.status == "interrupted":
            raise Interrupt(result.interrupt_payload)
        if result.status == "failed":
            raise RuntimeError(f"子图 {spec.name} 失败: {result.error}")
        # 5) output_map: 子 state → 父 partial update（仅映射存在的字段）
        return {
            parent_key: result.state[child_key]
            for child_key, parent_key in spec.output_map.items()
            if child_key in result.state
        }

    subgraph_node.__name__ = f"subgraph:{spec.name}"
    return subgraph_node
```

`add_subgraph` 内部调 `self.add_node(name, _make_subgraph_fn(spec))`——子图对外就是一个普通节点，`_edges` / `_cond` / `validate` / `to_mermaid` 全部自动工作。

---

## 3. 改动文件

| 文件 | 改动 | 说明 | 量 |
|------|------|------|----|
| `agentflow/graph.py` | `StateGraph.add_subgraph()` + `_SubgraphSpec` dataclass + `_make_subgraph_fn()` wrapper | 新增子图注册与 NodeFn 适配 | ~90 行 |
| `agentflow/__init__.py` | 无新导出（`add_subgraph` 是 StateGraph 方法） | — | 0 |
| `demo/demo_subgraph.py` | 场景 9：planner → code_review 子图（analyze + human_gate + summarize）→ END | 展示子图独立运行 + 中断冒泡 + 结果回传 | ~50 行 |
| `demo/__main__.py` | +1 行 `run_subgraph()` 调用 | — | 1 行 |
| `demo/common.py` | 不动（场景 9 用纯 Python API，不走 JSON config） | — | 0 |
| `test/test_subgraph.py` | 新增 6 个测试（对应 plan 验收 + 不变量） | 见 §5 | ~140 行 |

### 不动的部分（关键）

- `agentflow/graph_config.py` / `conf/*.json` —— P2-3 plan 只要求 Python API `add_subgraph`。JSON config 层声明式无法表达"已编译的 CompiledGraph"对象，本轮不扩展 JSON schema（避免引入"在 JSON 里声明子图"的复杂度，留独立 PR）
- `agentflow/state.py` / `checkpoint.py` / `interrupt.py` —— 完全复用现有 reducer / checkpointer / Interrupt
- `CompiledGraph._run_loop` 主循环 —— 不改一行

---

## 4. 实现要点

### 4.1 `add_subgraph` 注册

`StateGraph.__init__` 新增 `self._subgraphs: Dict[str, _SubgraphSpec] = {}`。`add_subgraph`：

1. 校验 `subgraph` 是 `CompiledGraph` 实例（类型错则 `TypeError`）
2. 校验 `name` 不与 `START`/`END` 冲突、不与已注册节点重名（复用 `add_node` 现有校验）
3. 构造 `_SubgraphSpec` 存入 `self._subgraphs`
4. 调 `self.add_node(name, _make_subgraph_fn(spec))`——对外就是一个普通节点

```python
@dataclass
class _SubgraphSpec:
    name: str
    subgraph: CompiledGraph
    input_map: Dict[str, str]
    output_map: Dict[str, str]
    max_steps: Optional[int]
```

### 4.2 `compile` 校验

`compile()` 已校验所有 `_nodes` 引用合法。子图作为普通节点注册后自动通过校验。无需额外改动。

### 4.3 `to_mermaid` 渲染

子图节点显示为双框区分普通节点：

```python
for n in sorted(self._nodes.keys()):
    if n in self._subgraphs:
        lines.append(f'    {safe(n)}[["子图: {n}"]]')
    else:
        lines.append(f'    {safe(n)}["{n}"]')
```

无需改主循环逻辑，只在 `to_mermaid` 里识别 `name in self._subgraphs` 时换 label。

### 4.4 max_steps 继承

子图 max_steps 由子图自己的 StateGraph 决定（`sub.compile()` 时已固定），与父图独立。
调用方负责在子图 `StateGraph(max_steps=N)` 构造时指定。

---

## 5. 测试用例（test/test_subgraph.py）

| # | 测试 | 验证 |
|---|------|------|
| 1 | `test_subgraph_runs_and_returns_output` | 子图 analyze 节点产出 → output_map 写回父 `state["review_result"]` |
| 2 | `test_subgraph_interrupt_bubbles_to_parent` | 子图内 `ctx.interrupt()` → 父图 `status="interrupted"` + payload 透传；`resume` 后父子都 `completed` |
| 3 | `test_subgraph_max_steps_failure_bubbles` | 子图 `max_steps=2` 死循环 → 父图 `status="failed"`，error 含"子图" |
| 4 | `test_subgraph_state_isolation` | `input_map`/`output_map` 之外的父 state 字段不被子图污染；子图内部 state 不泄漏到父 |
| 5 | `test_nested_two_level_subgraph` | 父图含子图 A，A 内再嵌子图 B → 端到端 `completed`，B 的 output 经 A 冒泡到父 |
| 6 | `test_subgraph_no_rerun_on_parent_resume` | 子图内 `a→gate(interrupt)→b`；父中断后 resume → 子图 `a` 不重跑（counter 不增），`gate` 重入一次，`b` 首次执行 |

测试 6 是硬不变量（`test/test_invariants.py` 的"恢复时已完成节点绝不重跑"）的子图版延伸，复用 `Counter` 模式。

测试文件末尾仿 `test_send.py` 提供 `ALL_TESTS` 列表 + `__main__` 入口，便于 `python test/test_subgraph.py` 单独跑。

---

## 6. demo 场景 9（demo/demo_subgraph.py）

```python
"""Scenario 9: subgraph — a node that is itself a nested StateGraph."""
from agentflow import Checkpointer, Command, START, END, StateGraph, StateSchema
from agentflow.graph import append_reducer  # via StateSchema reducers

from .common import banner


def analyze_fn(state, ctx):
    code = state.get("code", "")
    return {"ai_review": f"分析完成: {code[:20]}...", "log": ["[analyze] 完成"]}

def gate_fn(state, ctx):
    decision = ctx.interrupt({"ask": "请评审", "ai_review": state.get("ai_review")})
    return {"approved": bool(decision.get("approve")) if isinstance(decision, dict) else bool(decision),
            "log": [f"[gate] 决策: {decision}"]}

def summarize_fn(state, ctx):
    return {"result": f"review done, approved={state.get('approved')}",
            "log": ["[summarize] 汇总"]}

def prepare_fn(state, ctx):
    return {"code": "def fib(n): ...", "task": "review fib", "log": ["[prepare] 就绪"]}


def run_subgraph() -> None:
    banner("场景 9 — 子图：节点内嵌套 StateGraph，interrupt 冒泡到父图")
    cp = Checkpointer()

    # 子图: analyze → human_gate → summarize
    sub = StateGraph(StateSchema(reducers={"log": append_reducer}))
    sub.add_node("analyze", analyze_fn)
    sub.add_node("human_gate", gate_fn)
    sub.add_node("summarize", summarize_fn)
    sub.add_edge(START, "analyze")
    sub.add_edge("analyze", "human_gate")
    sub.add_edge("human_gate", "summarize")
    sub.add_edge("summarize", END)

    # 父图: prepare → code_review(子图) → END
    main = StateGraph(StateSchema(reducers={"log": append_reducer}))
    main.add_node("prepare", prepare_fn)
    main.add_subgraph("code_review", sub.compile(cp),
                      input_map={"code": "code", "task": "task"},
                      output_map={"result": "review_result"})
    main.add_edge(START, "prepare")
    main.add_edge("prepare", "code_review")
    main.add_edge("code_review", END)

    app = main.compile(cp)
    tid = "demo-subgraph"

    r1 = app.invoke({}, thread_id=tid)
    assert r1.status == "interrupted", r1.status
    print(f"\n→ 首次运行: status={r1.status}, 中断 payload 含 ai_review")
    print(f"  payload: {r1.interrupt_payload}")

    r2 = app.invoke({}, thread_id=tid, command=Command(resume={"approve": True}))
    assert r2.status == "completed", r2.status
    print(f"\n→ 恢复运行: status={r2.status}, step={r2.step}")
    print(f"  review_result: {r2.state.get('review_result')}")
    for line in r2.state.get("log", []):
        print(f"    {line}")


if __name__ == "__main__":
    run_subgraph()
```

`demo/__main__.py` 在 `run_dynamic_send()` 后加 `run_subgraph()`。

---

## 7. 验收标准

1. `PYTHONPATH=. python -m pytest test/ -v` 全过（现有 154 + 新增 6 = 160）
2. `python -m demo` 全过（新增场景 9，`__main__.py` +1 行）
3. `PYTHONPATH=. python test/test_invariants.py` 硬不变量仍通过
4. `PYTHONPATH=. python test/test_subgraph.py` 单独跑全过
5. `./scripts/verify_py37.sh` 在 Python 3.7 下全过（无新语法依赖：dataclass / f-string / typing 都已用）
6. 子图内中断 → 父图 resume → **子图已完成节点不重跑**（测试 6 守护）

---

## 8. 分支与协作流程

- 分支：`feat/p2-3-subgraph`（基于 `master`，独立于当前 `fix/cr-backlog-round1`）
- 流程（与 P1/P2-1 一致，**CR 不可跳过**）：
  1. Dev Agent 基于 `master` 创建 `feat/p2-3-subgraph`
  2. Dev 按本计划实现 `graph.py` + `test/test_subgraph.py` + `demo/demo_subgraph.py`，自测通过后 commit
  3. CR Agent 检出该分支，读本 plan → `git diff` → 跑测试 → 对抗性 fuzz（嵌套 3 层、子图内 Send、resume 后 activity cache 命中）→ 产出 `docs/review-notes-p2-3.md`
  4. Dev 根据 review-notes 修 bug，再次 commit
  5. CR 确认修复后标记"审查通过"
  6. PM 从 `master` 执行 merge，验证后删除 feature 分支

> ⚠️ PM 合并必须在 CR 通过之后。此规则来自三次教训（Wave 1/2/Round 1 跳过 CR 事后均发现 P0/P1），见 `AGENTS.md`。

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 子图 thread_id 碰撞（同名子图多实例） | thread_id 含 `ctx.step` + `ctx.instance_id`；Send worker 场景 `instance_id` 已稳定（见 `test_send.py:177`） |
| 子图内 activity cache 与父图冲突 | 子图用独立 thread_id，`activity_results` 表 PK 含 `thread_id`，天然隔离 |
| 子图 max_steps 超限被误判为父图 bug | failed 时 error 前缀 `"子图 {name} 失败:"`，区分父/子失败 |
| 子图 interrupt_payload 不可 JSON 序列化 | checkpointer 已用 `json.dumps(..., default=str)` 兜底；与现有 interrupt 语义一致 |
| `output_map` 指向不存在的 child key | wrapper 检查 `if child_key in result.state`，缺失则跳过（不抛错，与"部分更新"语义一致） |
| 子图内 Send 动态扇出 | 天然支持（子图是完整 CompiledGraph），本轮不专门加测试，留给 CR fuzz |

---

## 10. 不在本轮范围

- **JSON config 声明子图**（`graph_config.py` 扩展）—— 需要设计"子图配置引用"语法（如 `{"subgraph": "name", "input_map": {...}}`），留独立 PR
- **子图内 Send 动态扇出的专项测试** —— 天然支持，不额外覆盖
- **子图递归深度限制** —— 依赖 Python 默认栈深度；如需可加 `_depth` 计数器
- **子图独立 checkpointer** —— 本轮一律共享父 cp；如需隔离可后续加 `add_subgraph(..., checkpointer=...)` 参数
