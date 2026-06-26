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
    "models": ["mock"], "default_model": "mock",
    "protocol": "mock",
}


@dataclass
class NodeLLMConfig:
    """单个节点的 LLM 配置。任一字段缺省时由 registry 用 defaults / provider 默认补全。"""

    provider: str = "mock"               # 配置中声明的 provider 名称
    protocol: str | None = None          # anthropic | openai/chat | openai/response | mock
    model: str | None = None
    default_model: str | None = None     # 从 defaults.default_model 或 provider.default_model 继承
    system: str | None = None            # system prompt / instructions
    api_key_env: str | None = None       # 读取 key 的环境变量名
    base_url: str | None = None          # SDK base_url（不含 endpoint 路径）
    timeout: float = 60.0

    def resolved(self, providers: dict[str, dict[str, Any]] = None) -> "NodeLLMConfig":
        """用 provider 默认值补全空字段，返回新对象。providers 优先，mock 兜底。

        model 继承链（优先级从高到低）：
          nodes.model
          → defaults.default_model（由 registry 在合并时注入）
          → provider.default_model
          → provider.models[0]（列表第一项）
        """
        if (d := (providers or {}).get(self.provider)) is None and self.provider == "mock":
            d = _MOCK_PROVIDER
        if d is None:
            known = set((providers or {}).keys()) | {"mock"}
            raise ValueError(f"未知 provider: {self.provider}（支持: {', '.join(sorted(known))}）")
        models: list[str] = d.get("models") or []
        provider_default_model: str | None = d.get("default_model")
        # model 继承链
        model = self.model
        if not model:
            model = self.default_model or provider_default_model or (models[0] if models else "mock")
        return NodeLLMConfig(
            provider=self.provider,
            protocol=self.protocol or d.get("protocol", "mock"),
            model=model,
            system=self.system,
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
            system=cfg.system or "You are a helpful assistant.",
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        raise RuntimeError(f"Anthropic API 调用失败: {e}") from None
    parts = [b.text for b in response.content if b.type == "text"]
    return "".join(parts).strip()


def _call_openai_chat(cfg: NodeLLMConfig, prompt: str) -> str:
    """OpenAI Chat Completions API（通过 openai SDK）。"""
    if OpenAI is None:
        raise RuntimeError("openai SDK 未安装，请执行: pip install openai")
    key = _require_key(cfg)
    client = OpenAI(api_key=key, base_url=cfg.base_url, timeout=cfg.timeout)
    messages: list[dict[str, str]] = []
    if cfg.system:
        messages.append({"role": "system", "content": cfg.system})
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
            instructions=cfg.system or "You are a helpful assistant.",
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
              "default_model": "claude-sonnet-4-20250514",
              "protocol": "anthropic"
            },
            "openai_chat": {
              "base_url": "https://api.openai.com/v1",
              "api_key_env": "OPENAI_API_KEY",
              "models": ["gpt-4o", "gpt-4o-mini"],
              "default_model": "gpt-4o",
              "protocol": "openai/chat"
            },
            "openai_response": {
              "base_url": "https://api.openai.com/v1",
              "api_key_env": "OPENAI_API_KEY",
              "models": ["gpt-4o", "gpt-4o-mini"],
              "default_model": "gpt-4o",
              "protocol": "openai/response"
            }
          },
          "defaults": {"provider": "openai_chat", "temperature": 0.3, "default_model": "gpt-4o"},
          "nodes": {
            "planner":  {"provider": "anthropic", "system": "需求分析师"},
            "coder":    {"provider": "openai_chat", "system": "高级工程师"},
            "debugger": {"provider": "openai_chat"},
            "reviewer": {"provider": "mock"}
          }
        }

    model 继承链（优先级从高到低）：
      nodes[名称].model
      → defaults.default_model
      → provider.default_model
      → provider.models[0]
    """

    def __init__(self, defaults: dict[str, Any | None] = None,
                 nodes: dict[str, dict[str, Any | None]] = None,
                 providers: dict[str, dict[str, Any]] = None):
        self._defaults = defaults or {}
        self._nodes = nodes or {}
        self._providers = providers or {}

    @classmethod
    def from_file(cls, path: str) -> "LLMRegistry":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return cls(raw.get("defaults", {}), raw.get("nodes", {}), raw.get("providers", {}))

    @classmethod
    def load(cls, path: str | None = None) -> "LLMRegistry":
        """从文件加载；文件不存在则返回全 mock registry（demo 可离线跑）。"""
        path = path or os.environ.get("AGENTFLOW_LLM_CONFIG", "llm_config.json")
        if path and os.path.exists(path):
            return cls.from_file(path)
        return cls()

    def config_for(self, node: str) -> NodeLLMConfig:
        merged: dict[str, Any] = {}
        merged.update(self._defaults)
        merged.update(self._nodes.get(node, {}))
        # 将 defaults.default_model 注入为 default_model（resolved 会用它做回退）
        if "default_model" not in merged and "default_model" in self._defaults:
            merged["default_model"] = self._defaults["default_model"]
        valid = {f.name for f in fields(NodeLLMConfig)}
        merged = {k: v for k, v in merged.items() if k in valid}
        if not merged:
            merged = {"provider": "mock"}
        return NodeLLMConfig(**merged).resolved(self._providers)

    def complete(self, node: str, prompt: str, *, system: str | None = None) -> str:
        """对指定节点执行一次补全。system 入参可临时覆盖配置里的 system。"""
        cfg = self.config_for(node)
        if system is not None:
            cfg.system = system
        return _DISPATCH[cfg.protocol or "mock"](cfg, prompt)
