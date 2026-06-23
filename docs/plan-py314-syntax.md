# Python 3.14 语法现代化计划

## 目标

将现有代码从 Python 3.7 兼容语法系统性升级到 **Python 3.12+ 现代语法**，分支 `feat/py314-syntax-modernization`。

**注意**：此分支**不合并到 master**，作为未来迁移参考或独立发布用。

## 分支信息

| 项目 | 内容 |
|------|------|
| 分支名 | `feat/py314-syntax-modernization` |
| 基准 | `master` 最新（含 CR backlog 修复） |
| 合并策略 | **不合并 master**；PM 审查通过后打 tag |
| 工作流 | Dev → CR → PM（标准职责流程） |

## 现代化清单

### 1. 类型注解现代化 ✅

| 改动 | 说明 |
|------|------|
| 移除 `from __future__ import annotations` | 3.9+ 原生支持 PEP 585 |
| `typing.Dict` → `dict` | PEP 585 |
| `typing.List` → `list` | PEP 585 |
| `typing.Tuple` → `tuple` | PEP 585 |
| `typing.Optional[X]` → `X \| None` | PEP 604 |
| `typing.Union[X, Y]` → `X \| Y` | PEP 604 |
| 清理 `typing` 导入 | 仅保留 `Any`, `Callable`, `TypeVar` 等仍需的 |

**覆盖范围**：8 个 `agentflow/*.py` + 所有 `test/*.py` + 所有 `demo/*.py` + `demo.py`

### 2. match/case 重构 ✅

| 位置 | 改动 |
|------|------|
| `_normalize_frontier` | `if kind == "barrier"` → `match item: case dict(kind="barrier"):` |
| `_item_key` / `_item_label` | `if item.get("kind") == "barrier"` → `match item.get("kind"): case "barrier":` |
| `_exec_node` outcome | `if outcome["kind"] == "ok"` → `match outcome["kind"]: case "ok":` |

### 3. Walrus 运算符 ✅

| 位置 | 改动 |
|------|------|
| `agentflow/llm.py:44` | `if (d := (providers or {}).get(self.provider)) is None:` |
| `agentflow/nodes.py:141` | `if (workdir_explicit := "workdir" in state):` |
| `agentflow/graph.py:683` | `outs = out if isinstance(out := self._cond[node](state), (list, tuple)) else [out]` |
| `agentflow/checkpoint.py:114` | `if (row := ...) is None:` |
| `agentflow/checkpoint.py:139` | `if (row := ...) is None:` |
| `agentflow/graph.py:139` | `if (cached := ...) is not None:` |

### 4. f-string `=` 调试语法 ✅

| 位置 | 改动 |
|------|------|
| `agentflow/graph.py:121` | `f"[ctx.tool] WARN: ... {list(kwargs.keys())=}"` |
| `agentflow/plan.py:115` | `f"[plan] WARN: ... {plan.validate()=}"` |
| `agentflow/plan.py:132` | `f"[plan] WARN: ... {plan.validate()=}"` |

### 5. 测试/工具链同步 ✅

| 文件 | 说明 |
|------|------|
| `test/test_py314_compat.py` | 新建：强制现代语法模式（match/case、walrus、PEP 604/585、无 `__future__`） |
| `scripts/verify_py314.sh` | 新建：Python 3.14 真机验证脚本 |
| `README.md` | 更新版本引用为 Python 3.12+ |
| `docs/plan-py314-syntax.md` | 本文档 |

### 6. Demo 文件同步 ✅

所有 `demo/*.py` 和 `demo.py` 同步应用上述 1-4 项改动。

## 验证

```bash
# 现代语法检查
PYTHONPATH=. python3 test/test_py314_compat.py

# 全量测试
PYTHONPATH=. python3 -m pytest test/ -q

# Demo
PYTHONPATH=. python3 -m demo

# 或使用验证脚本
./scripts/verify_py314.sh
```

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| `match/case` 语义微差 | CR 阶段加 fuzz：随机 frontier 输入跑 1000 次 |
| 移除 `__future__` 后 forward reference 失效 | 保留已有的 `"NodeContext"` quoted strings |
| 3.14 环境差异 | 用本机 `/opt/homebrew/bin/python3`（3.14.6）做真机跑测 |

## 状态

| 阶段 | 状态 | 完成时间 |
|------|------|----------|
| Dev | ✅ 完成 | 2026-06-22 |
| CR | ⏳ 待审查 | - |
| PM | ⏳ 待审查 + 打 tag | - |
