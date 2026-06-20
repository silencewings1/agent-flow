# Demo 拆分 PM 计划

## Summary

将根目录单文件 `demo.py` 按功能拆分为 `demo/` 包下多个 `demo_xxx.py`，降低单文件维护成本，并保留历史入口兼容性。

## Scope

- 新建 `demo/` 包：
  - `common.py` 存放 demo 公共节点、router、registry、构图和 banner 工具。
  - 每个场景一个独立模块：`demo_pipeline.py`、`demo_parallel.py`、`demo_retry.py`、`demo_timetravel.py`、`demo_llm_config.py`、`demo_real_coder.py`、`demo_real_debugger.py`、`demo_dynamic_send.py`。
  - `__main__.py` 作为 `python -m demo` 总入口，顺序运行全部 8 个场景。
- 保留根目录 `demo.py` 作为兼容 wrapper，旧命令 `python demo.py` 仍可运行。
- 同步更新 README、AGENTS、CLAUDE、py37 验证脚本和 py37 AST 检查范围。

## Acceptance Criteria

- `python -m demo` 跑完 8 个场景。
- 每个独立 demo 模块可单独执行，例如 `python -m demo.demo_dynamic_send`。
- `python demo.py` 兼容入口继续可用。
- Python 3.7 AST/运行兼容验证覆盖 `demo/` 包。
- 全量测试不回归。

## Verification Commands

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m demo.demo_dynamic_send
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m demo
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python demo.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m pytest test/ -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 PYTHON37=/Users/ospacer/.py37/bin/python ./scripts/verify_py37.sh
```

## PM Status

- Dev implementation: completed in current working tree.
- PM smoke/full verification: completed locally, all commands passed.
- CR: PASS recorded in `docs/review-notes-demo-split.md`. PM may commit after final status check.
