# Demo 拆分 CR Checklist

## Review Scope

Review current working tree changes for splitting root `demo.py` into the new `demo/` package.

## Required Checks

- `demo/common.py` contains only shared demo helpers and does not accidentally retain scenario runner bodies that belong in per-scenario modules.
- Each scenario module owns one runnable scenario and can be executed independently with `python -m demo.demo_xxx`.
- `python -m demo` runs all 8 scenarios in the historical order.
- Root `demo.py` remains a compatibility wrapper for `python demo.py`.
- `CONFIG_PATH` still resolves correctly from inside `demo/common.py`.
- `scripts/verify_py37.sh` now uses `python -m demo`.
- `test/test_py37_compat.py` includes the new `demo/` package in AST compatibility checks.
- README/AGENTS/CLAUDE docs describe the new command and no misleading stale `python demo.py` primary command remains, except explicit compatibility wrapper text.

## Required Commands

```bash
git diff --check
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m demo.demo_pipeline
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m demo.demo_dynamic_send
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m demo
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python demo.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m pytest test/ -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 PYTHON37=/Users/ospacer/.py37/bin/python ./scripts/verify_py37.sh
```

## Output

Write review result to `docs/review-notes-demo-split.md` with PASS/FAIL and any findings.

## Follow-up: demo README Checks

- `demo/README.md` 存在于 `demo/` 目录内。
- 文档说明 `python -m demo` 总入口和 `python demo.py` 兼容入口。
- 文档覆盖全部 8 个场景文件：
  - `demo_pipeline.py`
  - `demo_parallel.py`
  - `demo_retry.py`
  - `demo_timetravel.py`
  - `demo_llm_config.py`
  - `demo_real_coder.py`
  - `demo_real_debugger.py`
  - `demo_dynamic_send.py`
- 每个场景的命令与模块名一致，并可从仓库根目录运行。
- 功能描述与对应 demo 实际行为一致。
- 公共模块 `common.py`、`__main__.py`、`__init__.py` 的说明不误导。
