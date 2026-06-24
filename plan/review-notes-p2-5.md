# CR Review: fix/cr-backlog-round2

## 审查范围

Commit `a56755b`（HEAD）：
- `docs/plan.md`
- `docs/plan-p2.md`
- `docs/plan-cr-backlog.md`
- `test/test_mcp.py`

## 审查方法

1. `git diff HEAD~1 HEAD` — 查看完整 diff
2. `PYTHONPATH=. python3 -m pytest test/ -q` — 跑全量测试
3. 静态阅读新增测试用例

## 测试结果

```
178 passed in 3.63s
```

全量通过，无回归。

## 逐文件审查

### `docs/plan.md`

- 新增「P0 任务完成状态」表格，列出 P0-1/P0-2/P0-3 的完成提交哈希。
- 备注中说明了 3 个 P0 任务分别对应的 commit 和实现要点。
- **结论**：纯文档，无代码影响。格式与现有文档风格一致。✅

### `docs/plan-p2.md`

- 任务总览表：`依赖` 列改为 `状态`，标记 P2-1~P2-4 为 `✅ 已完成`。
- P2-1/P2-2/P2-3/P2-4 各节末尾新增 `### 状态` 小节，标注完成 commit + CR 状态。
- P2-1 的 `关键设计` 表格被删除（实现已落地，设计决策不再需要）。
- 验收标准和当前状态段落同步更新。
- **结论**：文档与实际代码状态一致。删除已完成的设计决策表格是合理的，不会造成信息丢失（git 历史保留）。✅

### `docs/plan-cr-backlog.md`

- 顶部汇总表：P1 未修从 `10` 改为 `0`，增加说明「全部 10 个 P1 已在 commit `86f7ce7` 修复」。
- Round 1 小节：从「立刻修」改为「已完成」，保留问题清单但标记状态。
- 时间线更新。
- **结论**：与 `docs/plan.md` 的 P0 状态互相补充，信息一致。✅

### `test/test_mcp.py`

新增 4 个集成测试：

| 测试 | 验证点 | 结果 |
|------|--------|------|
| `test_graph_node_calls_mcp_tool_directly` | 节点内直接使用 `ToolRuntime` + `call_mcp` | ✅ |
| `test_graph_node_mcp_tool_with_ctx_tool_cache` | `ctx.tool()` 包装 MCP 调用，activity 缓存生效 | ✅ |
| `test_graph_with_multiple_mcp_providers` | 单节点注册多个 MCP 提供者，分别调用 | ✅ |
| `test_graph_mcp_tool_unknown_raises_cleanly` | 未知工具被 graph 捕获为 `status="failed"` | ✅ |

- 4 个新测试覆盖了 graph + MCP 的核心交互路径。
- `test_graph_mcp_tool_unknown_raises_cleanly` 修正了之前 Dev 版本中的错误假设（之前以为会直接抛 `ValueError`，实际 graph 会捕获并转为 `failed`）。
- 使用 `tempfile.gettempdir()` 作为 workdir，避免测试垃圾残留。

## 审查结论

**PASS** — 0 P0，0 P1，0 P2。

- 变更范围仅限文档和测试，无运行时行为修改。
- 178 个测试全过，无回归。
- 文档状态与实际代码/commit 历史一致。
