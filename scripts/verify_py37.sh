#!/bin/bash
# 验证代码在 Python 3.7 下能跑
# 注意：当前环境是 3.14，验证的是"代码不含 3.8+ 才支持的语法"。
# 真正的 3.7 跑测需要在 3.7 环境（CI/容器）跑此脚本。
set -e
cd "$(dirname "$0")/.."

# 选 Python 解释器：优先 3.7，其次 3.8，最后 fallback 到当前 python3
if command -v python3.7 >/dev/null 2>&1; then
    PY37=python3.7
elif command -v python3.8 >/dev/null 2>&1; then
    PY37=python3.8
else
    echo "[WARN] 没有 python3.7/3.8，回退到当前 python3（只做 AST + import 检查）"
    PY37=python3
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
echo "=== 7. review 测试 ==="
PYTHONPATH=. $PY37 test/test_review.py

echo
echo "=== 8. tools 测试 ==="
PYTHONPATH=. $PY37 test/test_tools.py

echo
echo "=== 9. coder 测试 ==="
PYTHONPATH=. $PY37 test/test_coder.py

echo
echo "=== 10. debugger 测试 ==="
PYTHONPATH=. $PY37 test/test_debugger.py

echo
echo "=== 11. demo 跑测 ==="
PYTHONPATH=. $PY37 demo.py

echo
echo "=== 全部通过 ==="
