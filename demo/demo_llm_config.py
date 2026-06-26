"""Scenario 5: per-node LLM provider/model resolution."""

from agentflow import LLMRegistry

from .common import banner


def run_llm_config() -> None:
    banner("场景 5 — 每节点 LLM 配置：provider/model 独立解析")
    reg = LLMRegistry(
        providers={
            "anthropic": {
                "base_url": "https://api.anthropic.com",
                "api_key_env": "ANTHROPIC_API_KEY",
                "models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514"],
                "protocol": "anthropic",
            },
            "openai_chat": {
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "models": ["gpt-4o", "gpt-4o-mini"],
                "protocol": "openai/chat",
            },
            "openai_response": {
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "models": ["gpt-4o", "gpt-4o-mini"],
                "protocol": "openai/response",
            },
        },
        nodes={
            "planner": {"provider": {"name": "anthropic", "model": "claude-sonnet-4-20250514"}},
            "coder": {"provider": {"name": "openai_chat", "model": "gpt-4o"}},
            "debugger": {"provider": {"name": "openai_chat", "model": "gpt-4o"}},
            "reviewer": {"provider": "mock"},
        },
    )
    print()
    for n in ("planner", "coder", "debugger", "reviewer"):
        c = reg.config_for(n)
        key = c.api_key_env or "-"
        print(f"    {n:9} provider={c.provider:12} protocol={c.protocol or '-':10} model={c.model:30} key_env={key}")
    print("\n  说明：所有 provider 定义均来自配置文件，代码中不再硬编码任何厂商。")
    print("       model 由 node 的 provider 对象直接指定，不再使用继承链。")
    print("       set_registry(reg) 即可让流水线节点按此配置调用真实 API；")
    print("       未设置 key 的真实 provider 会报清晰错误，mock 始终可离线运行。")


if __name__ == "__main__":
    run_llm_config()
