# Demo 使用说明

`demo/` 目录按功能拆分了 8 个可运行场景。所有 demo 均可在仓库根目录执行，推荐使用 Python 3.7 环境：

```bash
cd /Users/ospacer/cpp_test/agent-flow
source /Users/ospacer/.py37/bin/activate
```

## 跑全部场景

推荐入口：

```bash
PYTHONPATH=. python -m demo
```

兼容旧入口仍可用：

```bash
PYTHONPATH=. python demo.py
```

## 单独运行某个场景

| 文件 | 命令 | 功能 |
| --- | --- | --- |
| `demo_pipeline.py` | `PYTHONPATH=. python -m demo.demo_pipeline` | 场景 1：研发流水线，演示 planner → coder → debugger 测试回环 → AI review → human review HITL 中断/恢复。 |
| `demo_parallel.py` | `PYTHONPATH=. python -m demo.demo_parallel` | 场景 2：静态并行扇出，演示同一 super-step 多 worker 并发执行，以及 barrier join 汇聚。 |
| `demo_retry.py` | `PYTHONPATH=. python -m demo.demo_retry` | 场景 3：节点错误重试，演示节点前两次失败、第三次成功，并打印 `node_retry` / `node_ok` 事件日志。 |
| `demo_timetravel.py` | `PYTHONPATH=. python -m demo.demo_timetravel` | 场景 4：时间旅行 / checkpoint 历史，打印每个 super-step 的状态、frontier 和 `code_version`。 |
| `demo_llm_config.py` | `PYTHONPATH=. python -m demo.demo_llm_config` | 场景 5：每节点 LLM 配置解析，展示不同节点解析到的 provider、protocol、model、API key 环境变量名。 |
| `demo_real_coder.py` | `PYTHONPATH=. python -m demo.demo_real_coder` | 场景 6：真实 Coder 写文件到临时 `workdir`，验证 artifacts 文件实际落盘。 |
| `demo_real_debugger.py` | `PYTHONPATH=. python -m demo.demo_real_debugger` | 场景 7：真实 Debugger pytest 回环，先写入失败测试，再通过 dummy coder 修复后跑通。 |
| `demo_dynamic_send.py` | `PYTHONPATH=. python -m demo.demo_dynamic_send` | 场景 8：动态 Send/worker，演示 conditional router 动态生成多个同名 worker 实例，并用 `fanout` reducer 汇聚结果。 |

## 公共模块

- `common.py`：demo 公共节点、router、registry、构图工具和 banner 输出。
- `__main__.py`：`python -m demo` 的总入口，按场景 1 到 8 顺序运行所有 demo。
- `__init__.py`：将 `demo/` 标记为 Python package。

## 配置来源

多个场景通过 `demo/common.py` 读取仓库配置文件：

```text
/Users/ospacer/cpp_test/agent-flow/conf/graph_config.example.json
```

JSON 配置中定义了各 demo graph，例如 `pipeline`、`parallel`、`retry`、`timetravel`、`real_coder`、`real_debugger`、`dynamic_send`。
