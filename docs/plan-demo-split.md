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

## Follow-up: demo README

用户要求在 `demo/` 目录内增加说明文档，明确每个 `demo_xxx.py` 的用途和运行方式。

### Scope

- 新增 `demo/README.md`。
- 文档需覆盖：
  - `python -m demo` 总入口。
  - `python demo.py` 兼容入口。
  - 每个 `demo_xxx.py` 的单独运行命令和功能说明。
  - `common.py`、`__main__.py` 等公共模块说明。

### Acceptance Criteria

- `demo/README.md` 覆盖全部 8 个 demo 场景。
- 每条运行命令可从仓库根目录执行。
- 文档描述与实际 demo 文件职责一致。
- 至少完成一次 smoke test，例如 `python -m demo.demo_dynamic_send`。
- 经独立 CR PASS 后提交。
