#!/usr/bin/env bash
# 验证代码在 Python 3.7 下能跑
# 注意：当前环境是 3.14，验证的是"代码不含 3.8+ 才支持的语法"。
# 真正的 3.7 跑测需要在 3.7 环境（CI/容器）跑此脚本。
set -e
cd "$(dirname "$0")/.."

# 选 Python 解释器：必须 3.7 或 3.8。fallback 到 3.9+ 不算 3.7 兼容验证。
# CR 2026-06-18 2.1: 没有真 3.7/3.8 时必须 exit 1，不允许静默回退给"全绿"假象。
# CR Backlog 2026-06-18 2.3: 允许 CI 通过 PYTHON37 环境变量指定解释器路径。
if [ -n "${PYTHON37:-}" ] && command -v "$PYTHON37" >/dev/null 2>&1; then
    PY37="$PYTHON37"
elif command -v python3.7 >/dev/null 2>&1; then
    PY37=python3.7
elif command -v python3.8 >/dev/null 2>&1; then
    PY37=python3.8
else
    echo "[ERROR] 没有 python3.7/3.8，无法验证 3.7 兼容性。"
    echo "        当前 python3 是：$(python3 --version 2>&1)"
    echo "        请在 CI 镜像或容器中用 3.7/3.8 跑此脚本。"
    echo "        不要用 3.9+ 跑——本脚本的目的是确认 3.7 兼容，3.9+ 跑过不能证明 3.7 兼容。"
    exit 1
fi

echo "=== 使用解释器: $($PY37 --version) ==="

echo
echo "=== 1. import 链路检查 ==="
$PY37 -c "import sys; sys.path.insert(0, '.'); import agentflow; print('  agentflow import OK')"

echo
echo "=== 2. py37 兼容性测试（AST + 关键 import） ==="
PYTHONPATH=. $PY37 test/test_py37_compat.py

echo
echo "=== 3. 不变量测试 ==="
PYTHONPATH=. $PY37 test/test_invariants.py

echo
echo "=== 4. activity 缓存测试 ==="
PYTHONPATH=. $PY37 test/test_activity.py

echo
echo "=== 5. graph 测试 ==="
PYTHONPATH=. $PY37 test/test_graph.py

echo
echo "=== 6. planner 测试 ==="
PYTHONPATH=. $PY37 test/test_planner.py

echo
echo "=== 7. Send/worker 测试 ==="
PYTHONPATH=. $PY37 test/test_send.py

echo
echo "=== 8. review 测试 ==="
PYTHONPATH=. $PY37 test/test_review.py

echo
echo "=== 9. tools 测试 ==="
PYTHONPATH=. $PY37 test/test_tools.py

echo
echo "=== 10. coder 测试 ==="
PYTHONPATH=. $PY37 test/test_coder.py

echo
echo "=== 11. debugger 测试 ==="
PYTHONPATH=. $PY37 test/test_debugger.py

echo
echo "=== 12. demo 跑测 ==="
PYTHONPATH=. $PY37 demo.py

echo
echo "=== 全部通过 ==="
