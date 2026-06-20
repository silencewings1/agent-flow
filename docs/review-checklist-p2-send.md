# P2-1 Send/Worker CR 审查清单

> 范围：`codex/p2-send-worker` 分支相对 `master`，Dev commit `da67cee`。
> 目标：独立验证动态 Send/worker、严格 barrier、JSON graph_config 支持，不破坏 checkpoint/resume 不变量和 Python 3.7 兼容。

## 必跑命令

```bash
git diff --check master..HEAD
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m pytest test/ -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 PYTHON37=/Users/ospacer/.py37/bin/python ./scripts/verify_py37.sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python demo.py
```

## 核心审查点

- `Send` 只能由 conditional router 返回；普通节点仍只返回 `dict` / `None`。
- 同名 worker 多实例必须同时执行，且每个实例读取自己的 `Send.arg`。
- `Send.arg` 不能自动写回全局 state，只能通过 worker 返回 update 合并。
- `ctx.instance_id` 必须稳定、可读，且能区分同一节点的多个 Send 实例。
- activity/tool cache 必须按 Send worker 实例隔离；同名 worker 多实例不能命中同一条缓存。
- checkpoint frontier 对象必须能 JSON 持久化；resume 后不能重跑已完成节点。
- 旧字符串 frontier checkpoint 兼容归一化路径不能破坏现有线程恢复。
- `add_edge(["a", "b"], "join")` 必须是严格 barrier：即使 a/b 跨 super-step 完成，也要等两者都 ready 后才调度 join。
- barrier 等待记录必须持久化在 frontier，不能只存在内存。
- router 返回缺失 Send target 或未定义普通节点时，运行应失败并给出可定位错误。
- `fanout_reducer` 只接受 dict，并按 `{instance_id: payload}` 合并。
- JSON graph_config 支持 `"fanout"` reducer 和 `{"from": ["a", "b"], "to": "join"}`，非法 source/target 应抛带 graph 上下文的 `ValueError`。
- `conf/graph_config.example.json` 的 `dynamic_send` 示例通过 `demo.py` 跑通，且不引入 JSON 动态 import/eval。

## 对抗性 fuzz 建议

- router 返回 `[Send("w", {"id": 1}, key="x"), Send("w", {"id": 2}, key="x")]`：确认重复 key 的语义是否符合实现预期，至少不能导致 crash。
- router 返回 `Send("w", {"unjsonable": object()})`：确认 instance_id fallback 不 crash，或明确错误可定位。
- barrier sources 包含重复节点：`add_edge(["a", "a"], "join")`，确认是否去重或给出 warning。
- 中断发生在 Send worker 批次中第一个 worker：resume 后同批其他 worker 是否按当前设计重跑，是否破坏“已完成节点不重跑” invariant。
- 多个 barrier 指向同一 join：确认不会重复调度 join。

## CR 输出要求

CR 结果写入 `docs/review-notes.md`，标题建议：

```markdown
## P2-1 Send/Worker CR — codex/p2-send-worker（YYYY-MM-DD）
```

结论必须明确：

- PASS：可交 PM 合并。
- FAIL：列出 P0/P1/P2 问题，Dev 修复后再审。

PM 在 CR PASS 前不得 merge。
