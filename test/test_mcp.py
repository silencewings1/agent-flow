"""P2-4: MCP 工具适配 — 验证 MCPToolProvider ABC + ToolRuntime.register_mcp。"""
from __future__ import annotations

from agentflow import MCPToolProvider, ToolRuntime


# —— Mock MCP 提供者 —— #


class _MockWeatherProvider(MCPToolProvider):
    """模拟天气 MCP 服务。"""

    def __init__(self):
        self._tools = [
            {"name": "get_weather", "description": "查询天气", "input_schema": {"city": str}},
            {"name": "get_forecast", "description": "天气预报", "input_schema": {"city": str, "days": int}},
        ]

    def list_tools(self):
        return list(self._tools)

    def call_tool(self, name, arguments):
        if name == "get_weather":
            city = arguments.get("city", "?")
            return {"city": city, "temp_c": 25, "condition": "sunny"}
        if name == "get_forecast":
            city = arguments.get("city", "?")
            days = arguments.get("days", 1)
            return {"city": city, "days": days, "forecast": ["sunny"] * days}
        raise ValueError(f"未知工具: {name}")


class _BrokenProvider(MCPToolProvider):
    """模拟故障提供者：list_tools 抛异常。"""

    def list_tools(self):
        raise RuntimeError("provider down")

    def call_tool(self, name, arguments):
        raise RuntimeError("provider down")


# —— 测试 1：注册 + list_tools —— #


def test_register_mcp_and_list_tools():
    rt = ToolRuntime(thread_id="mcp-1")
    provider = _MockWeatherProvider()

    rt.register_mcp(provider)
    tools = rt.list_mcp_tools()

    assert len(tools) == 2
    names = {t["name"] for t in tools}
    assert names == {"get_weather", "get_forecast"}


def test_register_mcp_is_idempotent():
    """同一提供者多次注册不应重复。"""
    rt = ToolRuntime(thread_id="mcp-idem")
    provider = _MockWeatherProvider()

    rt.register_mcp(provider)
    rt.register_mcp(provider)
    rt.register_mcp(provider)

    tools = rt.list_mcp_tools()
    assert len(tools) == 2  # 不膨胀


def test_register_multiple_providers():
    """多个提供者工具列表聚合。"""
    rt = ToolRuntime(thread_id="mcp-multi")

    class _SecondProvider(MCPToolProvider):
        def list_tools(self):
            return [{"name": "calc", "description": "计算器"}]

        def call_tool(self, name, arguments):
            return {"result": 42}

    rt.register_mcp(_MockWeatherProvider())
    rt.register_mcp(_SecondProvider())

    tools = rt.list_mcp_tools()
    names = {t["name"] for t in tools}
    assert names == {"get_weather", "get_forecast", "calc"}


def test_broken_provider_does_not_break_others():
    """单个提供者故障不影响其他提供者。"""
    rt = ToolRuntime(thread_id="mcp-broken")
    rt.register_mcp(_BrokenProvider())
    rt.register_mcp(_MockWeatherProvider())

    tools = rt.list_mcp_tools()
    names = {t["name"] for t in tools}
    assert names == {"get_weather", "get_forecast"}


# —— 测试 2：call_tool —— #


def test_call_mcp_tool_returns_result():
    rt = ToolRuntime(thread_id="mcp-call")
    rt.register_mcp(_MockWeatherProvider())

    result = rt.call_mcp("get_weather", {"city": "北京"})
    assert result["city"] == "北京"
    assert result["temp_c"] == 25
    assert result["condition"] == "sunny"


def test_call_mcp_tool_with_arguments():
    rt = ToolRuntime(thread_id="mcp-call-args")
    rt.register_mcp(_MockWeatherProvider())

    result = rt.call_mcp("get_forecast", {"city": "上海", "days": 3})
    assert result["city"] == "上海"
    assert result["days"] == 3
    assert len(result["forecast"]) == 3


def test_call_mcp_unknown_tool_raises():
    rt = ToolRuntime(thread_id="mcp-unknown")
    rt.register_mcp(_MockWeatherProvider())

    try:
        rt.call_mcp("nonexistent_tool", {})
        assert False, "应抛 ValueError"
    except ValueError as exc:
        assert "nonexistent_tool" in str(exc)


def test_call_mcp_without_providers_raises():
    rt = ToolRuntime(thread_id="mcp-empty")

    try:
        rt.call_mcp("anything", {})
        assert False, "应抛 ValueError"
    except ValueError as exc:
        assert "anything" in str(exc)


ALL_TESTS = [
    test_register_mcp_and_list_tools,
    test_register_mcp_is_idempotent,
    test_register_multiple_providers,
    test_broken_provider_does_not_break_others,
    test_call_mcp_tool_returns_result,
    test_call_mcp_tool_with_arguments,
    test_call_mcp_unknown_tool_raises,
    test_call_mcp_without_providers_raises,
]


if __name__ == "__main__":
    import sys

    failed = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"✅ {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"❌ {test.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"💥 {test.__name__}: {type(exc).__name__}: {exc}")
    if failed:
        sys.exit(1)
    print(f"\n✅ 全部 {len(ALL_TESTS)} 个 MCP 测试通过\n")
