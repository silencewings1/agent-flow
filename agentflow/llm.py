"""LLM provider 抽象 + 每节点配置层。

设计目标：
- 接入真实 API 的地方做成**配置文件**（JSON），每个节点可配置自己的 provider/模型/参数；
- 同时支持 **Anthropic Messages**、**OpenAI Chat Completions**、**OpenAI Responses** 三种协议；
- 通过配置文件顶层的 `providers` 字段声明项目支持哪些厂商，`protocol` 字段区分协议类型；
- 未配置或配置为 mock 时退化为离线桩，demo 无需任何 key 即可运行。

协议类型：
    anthropic        — Anthropic Messages API（通过 anthropic SDK）
    openai/chat      — OpenAI Chat Completions API（通过 openai SDK）
    openai/response  — OpenAI Responses API（通过 openai SDK）
    mock             — 离线桩

安全：API Key 默认从**环境变量**读取（api_key_env 指定变量名），不落配置文件。
"""

import json
import os
from dataclasses import dataclass, fields
from typing import Any

# ── SDK imports ──────────────────────────────────────────────────────────
try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment,misc]

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None  # type: ignore[assignment,misc]


_MOCK_PROVIDER: dict[str, Any] = {
    "base_url": "", "api_key_env": "",
    "models": ["mock"],
    "protocol": "mock",
}


@dataclass
class NodeLLMConfig:
    """单个节点的 LLM 配置。provider 为厂商名，model 由节点直接指定。"""

    provider: str = "mock"               # 配置中声明的 provider 名称
    protocol: str | None = None          # anthropic | openai/chat | openai/response | mock
    model: str | None = None
    prompt: str | None = None            # 配置文件中的定制指令，每次调用时前置到 prompt
    system_prompt: str | None = None     # 每次创建节点时提交给 LLM 的提示词
    api_key_env: str | None = None       # 读取 key 的环境变量名
    base_url: str | None = None          # SDK base_url（不含 endpoint 路径）
    timeout: float = 60.0

    def resolved(self, providers: dict[str, dict[str, Any]] = None) -> "NodeLLMConfig":
        """用 provider 默认值补全空字段，返回新对象。mock 兜底。"""
        if (d := (providers or {}).get(self.provider)) is None and self.provider == "mock":
            d = _MOCK_PROVIDER
        if d is None:
            known = set((providers or {}).keys()) | {"mock"}
            raise ValueError(f"未知 provider: {self.provider}（支持: {', '.join(sorted(known))}）")
        models: list[str] = d.get("models") or []
        model = self.model or (models[0] if models else "mock")
        return NodeLLMConfig(
            provider=self.provider,
            protocol=self.protocol or d.get("protocol", "mock"),
            model=model,
            prompt=self.prompt,
            system_prompt=self.system_prompt,
            api_key_env=self.api_key_env or d["api_key_env"],
            base_url=self.base_url or d["base_url"],
            timeout=self.timeout,
        )


def _require_key(cfg: NodeLLMConfig) -> str:
    key = os.environ.get(cfg.api_key_env or "")
    if not key:
        raise RuntimeError(
            f"provider={cfg.provider} 需要 API Key，但环境变量 {cfg.api_key_env} 未设置。"
        )
    return key


# ── SDK callers ──────────────────────────────────────────────────────────

def _call_anthropic(cfg: NodeLLMConfig, prompt: str) -> str:
    """Anthropic Messages API（通过 anthropic SDK）。"""
    if Anthropic is None:
        raise RuntimeError("anthropic SDK 未安装，请执行: pip install anthropic")
    key = _require_key(cfg)
    client = Anthropic(api_key=key, base_url=cfg.base_url, timeout=cfg.timeout)
    try:
        response = client.messages.create(
            model=cfg.model,
            max_tokens=2048,
            system=cfg.system_prompt or "You are a helpful assistant.",
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        raise RuntimeError(f"Anthropic API 调用失败: {e}") from None
    parts = [b.text for b in response.content if b.type == "text"]
    result = "".join(parts).strip()
    if not result:
        # 深度思考模型可能只返回 ThinkingBlock 无 TextBlock
        thinking = [b.thinking for b in response.content if b.type == "thinking"]
        if thinking:
            result = "".join(thinking).strip()
    return result


def _call_openai_chat(cfg: NodeLLMConfig, prompt: str) -> str:
    """OpenAI Chat Completions API（通过 openai SDK）。"""
    if OpenAI is None:
        raise RuntimeError("openai SDK 未安装，请执行: pip install openai")
    key = _require_key(cfg)
    client = OpenAI(api_key=key, base_url=cfg.base_url, timeout=cfg.timeout)
    messages: list[dict[str, str]] = []
    if cfg.system_prompt:
        messages.append({"role": "system", "content": cfg.system_prompt})
    messages.append({"role": "user", "content": prompt})
    try:
        response = client.chat.completions.create(
            model=cfg.model,
            messages=messages,
            max_tokens=2048,
        )
    except Exception as e:
        raise RuntimeError(f"OpenAI Chat API 调用失败: {e}") from None
    return response.choices[0].message.content.strip()


def _call_openai_response(cfg: NodeLLMConfig, prompt: str) -> str:
    """OpenAI Responses API（通过 openai SDK）。"""
    if OpenAI is None:
        raise RuntimeError("openai SDK 未安装，请执行: pip install openai")
    key = _require_key(cfg)
    client = OpenAI(api_key=key, base_url=cfg.base_url, timeout=cfg.timeout)
    try:
        response = client.responses.create(
            model=cfg.model,
            input=prompt,
            instructions=cfg.system_prompt or "You are a helpful assistant.",
            max_output_tokens=2048,
        )
    except Exception as e:
        raise RuntimeError(f"OpenAI Responses API 调用失败: {e}") from None
    return response.output_text.strip()


def _call_mock(cfg: NodeLLMConfig, prompt: str) -> str:
    """离线桩：返回确定性文本，便于无 key 跑 demo / 测试。"""
    head = prompt.strip().splitlines()[0] if prompt.strip() else ""
    return f"[mock:{cfg.model}] 针对「{head[:40]}」的生成结果"


_DISPATCH = {
    "anthropic":        _call_anthropic,
    "openai/chat":      _call_openai_chat,
    "openai/response":  _call_openai_response,
    "mock":             _call_mock,
}


class LLMRegistry:
    """加载配置、按节点名解析 LLM 配置并执行补全。

    配置文件结构（JSON）：
        {
          "providers": {
            "anthropic": {
              "base_url": "https://api.anthropic.com",
              "api_key_env": "ANTHROPIC_API_KEY",
              "models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514"],
              "protocol": "anthropic"
            },
            "openai_chat": {
              "base_url": "https://api.openai.com/v1",
              "api_key_env": "OPENAI_API_KEY",
              "models": ["gpt-4o", "gpt-4o-mini"],
              "protocol": "openai/chat"
            },
            "openai_response": {
              "base_url": "https://api.openai.com/v1",
              "api_key_env": "OPENAI_API_KEY",
              "models": ["gpt-4o", "gpt-4o-mini"],
              "protocol": "openai/response"
            }
          },
          "nodes": {
            "planner":  {"provider": {"name": "anthropic", "model": "claude-sonnet-4-20250514"}},
            "coder":    {"provider": {"name": "openai_chat", "model": "gpt-4o"}},
            "debugger": {"provider": {"name": "openai_chat", "model": "gpt-4o"}},
            "reviewer": {"provider": "mock"}
          }
        }
    """

    def __init__(self, nodes: dict[str, dict[str, Any | None]] = None,
                 providers: dict[str, dict[str, Any]] = None):
        self._nodes = nodes or {}
        self._providers = providers or {}

    @classmethod
    def from_file(cls, path: str) -> "LLMRegistry":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return cls(raw.get("nodes", {}), raw.get("providers", {}))

    @classmethod
    def load(cls, path: str | None = None) -> "LLMRegistry":
        """从文件加载；文件不存在则返回全 mock registry（demo 可离线跑）。"""
        path = path or os.environ.get("AGENTFLOW_LLM_CONFIG", "llm_config.json")
        if path and os.path.exists(path):
            return cls.from_file(path)
        return cls()

    def config_for(self, node: str) -> NodeLLMConfig:
        node_cfg = self._nodes.get(node, {})
        merged: dict[str, Any] = {}

        # provider 可以是 {name, model} 对象，或纯字符串（如 "mock"）
        provider_spec = node_cfg.get("provider", "mock")
        if isinstance(provider_spec, dict):
            merged["provider"] = provider_spec.get("name", "mock")
            if "model" in provider_spec:
                merged["model"] = provider_spec["model"]
        else:
            merged["provider"] = provider_spec

        # prompt 映射
        if "prompt" in node_cfg:
            merged["prompt"] = node_cfg["prompt"]

        valid = {f.name for f in fields(NodeLLMConfig)}
        merged = {k: v for k, v in merged.items() if k in valid}
        if not merged:
            merged = {"provider": "mock"}
        return NodeLLMConfig(**merged).resolved(self._providers)

    def complete(self, node: str, prompt: str, *, system_prompt: str | None = None) -> str:
        """对指定节点执行一次补全。

        system_prompt 可临时覆盖配置里的 system_prompt。
        配置文件中的 ``prompt`` 字段会自动前置到 ``prompt`` 参数之前。
        """
        cfg = self.config_for(node)
        if system_prompt is not None:
            cfg.system_prompt = system_prompt
        if cfg.prompt:
            prompt = cfg.prompt + "\n\n" + prompt
        return _DISPATCH[cfg.protocol or "mock"](cfg, prompt)
