# Demo 拆分 CR Review Notes

日期：2026-06-20
角色：独立 CR（代码审查及测试）
范围：当前工作区 demo.py → demo/ 包拆分改动

## 结论

PASS

未发现 P0/P1/P2 问题。拆分后的 demo 包满足计划和 checklist：8 个场景可通过 `python -m demo` 按历史顺序运行，根目录 `demo.py` 兼容入口可用，Python 3.7 兼容验证覆盖了新 `demo/` 包，文档命令已更新为主推 `python -m demo`。

## 静态审查结果

- `demo/common.py`：仅包含共享 demo helper、节点/路由注册、构图、banner 等公共逻辑；未保留场景 runner 主体。
- 场景模块：`demo/demo_pipeline.py`、`demo/demo_parallel.py`、`demo/demo_retry.py`、`demo/demo_timetravel.py`、`demo/demo_llm_config.py`、`demo/demo_real_coder.py`、`demo/demo_real_debugger.py`、`demo/demo_dynamic_send.py` 均各自拥有一个 `run_*()`，并带 `if __name__ == "__main__"` 独立入口。
- `demo/__main__.py`：按场景 1 → 8 顺序调用全部 runner：pipeline、parallel、retry、timetravel、llm_config、real_coder、real_debugger、dynamic_send。
- 根目录 `demo.py`：为兼容 wrapper，仅导入并调用 `demo.__main__.main()`。
- `CONFIG_PATH`：`demo/common.py` 使用 `os.path.dirname(os.path.dirname(__file__))` 回到仓库根目录再定位 `conf/graph_config.example.json`，路径正确。
- `scripts/verify_py37.sh`：demo 跑测命令已改为 `PYTHONPATH=. $PY37 -m demo`。
- `test/test_py37_compat.py`：`_SOURCE_DIRS` 已包含 `demo`，AST 兼容检查覆盖新 demo 包。
- README/AGENTS/CLAUDE：主命令已更新为 `python -m demo`，未发现误导性的旧主命令；旧 `python demo.py` 仅作为兼容 wrapper 保留在代码注释中。
- Python 3.8+ 语法：py37 兼容测试通过，未发现不兼容语法。

## 执行命令结果

```bash
git diff --check
```
结果：PASS（无输出，退出码 0）

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m demo.demo_pipeline
```
结果：PASS。场景 1 成功运行至最终 `status=completed`，包含 HITL 中断/恢复与打回后再合并流程。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m demo.demo_dynamic_send
```
结果：PASS。场景 8 成功运行，`status=completed`，动态 worker fanout 汇聚 3 个产物。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m demo
```
结果：PASS。全部 8 个场景按 1→8 顺序执行并输出 `✅ 全部场景执行完毕`。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python demo.py
```
结果：PASS。兼容 wrapper 成功运行全部 8 个场景并输出 `✅ 全部场景执行完毕`。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m pytest test/ -q -p no:cacheprovider
```
结果：PASS。`155 passed in 3.46s`。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON37=/Users/ospacer/.py37/bin/python ./scripts/verify_py37.sh
```
结果：PASS。使用解释器 `Python 3.7.17`，脚本内 import、py37 AST/关键 import、不变量、activity、graph、planner、Send、review、tools、coder、debugger、`python -m demo` 全部通过。

## 额外独立运行抽查

除 checklist 明确要求的 `demo.demo_pipeline` 与 `demo.demo_dynamic_send` 外，额外独立运行以下场景模块，均 PASS：

- `demo.demo_parallel`
- `demo.demo_retry`
- `demo.demo_timetravel`
- `demo.demo_llm_config`
- `demo.demo_real_coder`
- `demo.demo_real_debugger`

## 发现问题

无。

## Follow-up: demo README review

日期：2026-06-20
角色：独立 CR（代码审查及测试）
范围：follow-up 文档改动：新增 `demo/README.md`，更新 `docs/plan-demo-split.md`、`docs/review-checklist-demo-split.md`

### 结论

PASS

未发现 P0/P1/P2 问题。`demo/README.md` 覆盖全部 8 个 `demo_xxx.py`，各运行命令与模块名一致且按文档前置条件可从仓库根目录执行；功能描述与对应 demo 实际行为一致；已说明 `python -m demo` 总入口和 `python demo.py` 兼容入口；`common.py`、`__main__.py`、`__init__.py` 说明准确；未发现明显 Markdown/命令错误。

### 静态审查

- 覆盖完整性：README 表格覆盖 `demo_pipeline.py`、`demo_parallel.py`、`demo_retry.py`、`demo_timetravel.py`、`demo_llm_config.py`、`demo_real_coder.py`、`demo_real_debugger.py`、`demo_dynamic_send.py` 共 8 个场景。
- 命令一致性：每行命令均为 `PYTHONPATH=. python -m demo.<模块名>`，与对应文件名一致；README 顶部要求从仓库根目录执行，并给出 `cd /Users/ospacer/cpp_test/agent-flow`。
- 行为一致性：逐项对照 `demo/*.py` 与 `conf/graph_config.example.json`，场景描述与实际 runner/graph 行为一致，包括 pipeline HITL 恢复、parallel barrier join、retry event log、timetravel checkpoint history、LLM config 展示、real coder 落盘、real debugger pytest 回环、dynamic Send fanout 汇聚。
- 入口说明：README 明确推荐 `PYTHONPATH=. python -m demo`，并说明 `PYTHONPATH=. python demo.py` 为兼容旧入口。
- 公共模块说明：`common.py` 描述为公共节点/router/registry/构图/banner 输出，`__main__.py` 描述为 1 到 8 顺序总入口，`__init__.py` 描述为 package 标记，均准确。
- Markdown/命令格式：表格、代码块、反引号和命令文本未见明显格式错误。

### 命令结果

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m demo.demo_dynamic_send
```

结果：PASS（退出码 0）。关键输出：

```text
================================================================
  场景 8 — 动态 Send/worker：router 动态生成同名 worker 实例
================================================================

→ status=completed, step=3
  fanout keys: ['dynamic_worker:3a3c56322c59', 'dynamic_worker:5af600a11913', 'dynamic_worker:785e82288561']
    [dynamic_split] 动态生成 3 个 worker
    [dynamic_worker] api:实现 API
    [dynamic_worker] tests:补测试
    [dynamic_worker] docs:写文档
    [dynamic_join] 汇聚 3 个动态产物: ['api:实现 API', 'docs:写文档', 'tests:补测试']
```

补充抽查：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/ospacer/.py37/bin/python -m demo.demo_real_debugger
```

结果：PASS（退出码 0）。用于核对 README 中“先写入失败测试，再通过 dummy coder 修复后跑通”的描述；输出显示第一次测试失败、`[Coder] 修复 v2`、第二次测试通过。

### 问题列表

无。
