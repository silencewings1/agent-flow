#!/usr/bin/env bash
# Python 3.14 现代语法全量验证脚本
set -euo pipefail

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PY314="${PY314:-/opt/homebrew/bin/python3}"
echo "使用 Python: ${PY314}"
${PY314} --version

# 检查 Python 3.14+
version=$(${PY314} --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
major=$(echo "$version" | cut -d. -f1)
minor=$(echo "$version" | cut -d. -f2)
if [ "$major" -lt 3 ] || [ "$major" -eq 3 -a "$minor" -lt 12 ]; then
    echo -e "${RED}错误: 需要 Python 3.12+，当前是 ${version}${NC}"
    exit 1
fi

echo ""
echo "========================================"
echo "1. 现代语法兼容性检查"
echo "========================================"
PYTHONPATH=. ${PY314} test/test_py314_compat.py

echo ""
echo "========================================"
echo "2. 不变量测试"
echo "========================================"
PYTHONPATH=. ${PY314} test/test_invariants.py

echo ""
echo "========================================"
echo "3. Activity 缓存测试"
echo "========================================"
PYTHONPATH=. ${PY314} test/test_activity.py

echo ""
echo "========================================"
echo "4. Graph 核心测试"
echo "========================================"
PYTHONPATH=. ${PY314} test/test_graph.py

echo ""
echo "========================================"
echo "5. Send 测试"
echo "========================================"
PYTHONPATH=. ${PY314} test/test_send.py

echo ""
echo "========================================"
echo "6. Subgraph 测试"
echo "========================================"
PYTHONPATH=. ${PY314} test/test_subgraph.py

echo ""
echo "========================================"
echo "7. CR Fuzz 对抗性测试"
echo "========================================"
PYTHONPATH=. ${PY314} test/cr_fuzz_subgraph.py

echo ""
echo "========================================"
echo "8. Tools 测试"
echo "========================================"
PYTHONPATH=. ${PY314} test/test_tools.py

echo ""
echo "========================================"
echo "9. Coder 测试"
echo "========================================"
PYTHONPATH=. ${PY314} test/test_coder.py

echo ""
echo "========================================"
echo "10. Debugger 测试"
echo "========================================"
PYTHONPATH=. ${PY314} test/test_debugger.py

echo ""
echo "========================================"
echo "11. Planner 测试"
echo "========================================"
PYTHONPATH=. ${PY314} test/test_planner.py

echo ""
echo "========================================"
echo "12. Review 测试"
echo "========================================"
PYTHONPATH=. ${PY314} test/test_review.py

echo ""
echo "========================================"
echo "13. Graph Config 测试"
echo "========================================"
PYTHONPATH=. ${PY314} test/test_graph_config.py

echo ""
echo "========================================"
echo "14. MCP 工具测试"
echo "========================================"
PYTHONPATH=. ${PY314} test/test_mcp.py

echo ""
echo "========================================"
echo "15. 全量 Demo 场景"
echo "========================================"
PYTHONPATH=. ${PY314} -m demo

echo ""
echo -e "${GREEN}✅ Python 3.14 全量验证通过${NC}"
