"""P2-4: MCP 工具适配 — 验证 MCPToolProvider ABC + ToolRuntime.register_mcp。

集成测试（graph + MCP）：
- 节点在 StateGraph 内调用 MCP 工具
- MCP 工具与 ctx.tool() 缓存/审计协同
- 中断恢复后 MCP 调用仍可继续
"""

import os
import tempfile

from agentflow import (
    END,
    START,
    Checkpointer,
    MCPToolProvider,
    StateGraph,
    StateSchema,
    ToolRuntime,
)


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


# —— 单元测试：ToolRuntime 隔离 —— #


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


# —— 单元测试：call_mcp —— #


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


# —— 集成测试：graph + MCP —— #


def test_graph_node_calls_mcp_tool_directly():
    """节点内直接创建 ToolRuntime 并调用 MCP 工具。"""
    weather_calls = []

    class _CountingProvider(_MockWeatherProvider):
        def call_tool(self, name, arguments):
            weather_calls.append((name, arguments))
            return super().call_tool(name, arguments)

    def weather_node(state, ctx):
        rt = ToolRuntime(thread_id=ctx.thread_id, root=tempfile.gettempdir())
        rt.register_mcp(_CountingProvider())
        result = rt.call_mcp("get_weather", {"city": state.get("city", "?")})
        return {"weather": result}

    g = StateGraph()
    g.add_node("weather", weather_node)
    g.add_edge(START, "weather")
    g.add_edge("weather", END)

    res = g.compile(Checkpointer()).invoke({"city": "北京"}, thread_id="mcp-graph-1")
    assert res.status == "completed"
    assert res.state["weather"]["city"] == "北京"
    assert res.state["weather"]["temp_c"] == 25
    assert len(weather_calls) == 1


def test_graph_node_mcp_tool_with_ctx_tool_cache():
    """MCP 工具调用通过 ctx.tool() 包装，获得 activity 缓存。"""
    call_count = [0]

    class _CountingProvider(_MockWeatherProvider):
        def call_tool(self, name, arguments):
            call_count[0] += 1
            return super().call_tool(name, arguments)

    def weather_node(state, ctx):
        rt = ToolRuntime(thread_id=ctx.thread_id, root=tempfile.gettempdir())
        rt.register_mcp(_CountingProvider())
        # 用 ctx.tool 包装 MCP 调用，获得缓存
        def _call():
            return rt.call_mcp("get_weather", {"city": state.get("city", "?")})
        result = ctx.tool("mcp:weather", _call, key="beijing", input_summary="city=北京")
        return {"weather": result}

    g = StateGraph()
    g.add_node("weather", weather_node)
    g.add_edge(START, "weather")
    g.add_edge("weather", END)

    cp = Checkpointer()
    # 第一次执行
    res1 = g.compile(cp).invoke({"city": "北京"}, thread_id="mcp-graph-cache")
    assert res1.status == "completed"
    assert call_count[0] == 1

    # 再次执行（同 thread），缓存命中，call_tool 不再执行
    res2 = g.compile(cp).invoke({"city": "北京"}, thread_id="mcp-graph-cache")
    assert res2.status == "completed"
    assert call_count[0] == 1  # 不增加


def test_graph_with_multiple_mcp_providers():
    """图中节点同时使用多个 MCP 提供者。"""
    def multi_node(state, ctx):
        rt = ToolRuntime(thread_id=ctx.thread_id, root=tempfile.gettempdir())
        rt.register_mcp(_MockWeatherProvider())

        class _CalcProvider(MCPToolProvider):
            def list_tools(self):
                return [{"name": "add", "input_schema": {"a": int, "b": int}}]

            def call_tool(self, name, arguments):
                return {"sum": arguments["a"] + arguments["b"]}

        rt.register_mcp(_CalcProvider())

        weather = rt.call_mcp("get_weather", {"city": "北京"})
        calc = rt.call_mcp("add", {"a": 1, "b": 2})
        return {"weather": weather, "calc": calc}

    g = StateGraph()
    g.add_node("multi", multi_node)
    g.add_edge(START, "multi")
    g.add_edge("multi", END)

    res = g.compile(Checkpointer()).invoke({}, thread_id="mcp-graph-multi")
    assert res.status == "completed"
    assert res.state["weather"]["city"] == "北京"
    assert res.state["calc"]["sum"] == 3


def test_graph_mcp_tool_unknown_raises_cleanly():
    """MCP 工具未找到时抛 ValueError，graph 将其记录为 failed 状态。"""
    def bad_node(state, ctx):
        rt = ToolRuntime(thread_id=ctx.thread_id, root=tempfile.gettempdir())
        rt.register_mcp(_MockWeatherProvider())
        return rt.call_mcp("nonexistent", {})

    g = StateGraph()
    g.add_node("bad", bad_node)
    g.add_edge(START, "bad")
    g.add_edge("bad", END)

    res = g.compile(Checkpointer()).invoke({}, thread_id="mcp-graph-bad")
    assert res.status == "failed"
    assert "nonexistent" in (res.error or "")


ALL_TESTS = [
    # 单元测试
    test_register_mcp_and_list_tools,
    test_register_mcp_is_idempotent,
    test_register_multiple_providers,
    test_broken_provider_does_not_break_others,
    test_call_mcp_tool_returns_result,
    test_call_mcp_tool_with_arguments,
    test_call_mcp_unknown_tool_raises,
    test_call_mcp_without_providers_raises,
    # 集成测试
    test_graph_node_calls_mcp_tool_directly,
    test_graph_node_mcp_tool_with_ctx_tool_cache,
    test_graph_with_multiple_mcp_providers,
    test_graph_mcp_tool_unknown_raises_cleanly,
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