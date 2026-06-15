"""LLM provider 抽象 + 每节点配置层。

设计目标（对应用户需求）：
- 接入真实 API 的地方做成**配置文件**（JSON），每个节点可配置自己的 provider/模型/参数；
- 同时支持 **Claude (Anthropic)** 与 **OpenAI**；
- 未配置或配置为 mock 时退化为离线桩，demo 无需任何 key 即可运行。

依赖策略：沿用本项目「零三方依赖」原则，用标准库 urllib 直连两家 HTTP API，
不强制安装 anthropic / openai SDK。

安全：API Key 默认从**环境变量**读取（api_key_env 指定变量名），不落配置文件。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, fields
from typing import Any, Dict, Optional

# 各 provider 的默认 endpoint 与默认 key 环境变量名
_PROVIDER_DEFAULTS = {
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1/messages",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "claude-opus-4-8",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1/chat/completions",
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-4o",
    },
    "mock": {"base_url": "", "api_key_env": "", "model": "mock"},
}


@dataclass
class NodeLLMConfig:
    """单个节点的 LLM 配置。任一字段缺省时由 registry 用 defaults / provider 默认补全。"""

    provider: str = "mock"               # anthropic | openai | mock
    model: Optional[str] = None
    system: Optional[str] = None         # system prompt
    temperature: float = 0.7
    max_tokens: int = 2048
    api_key_env: Optional[str] = None     # 读取 key 的环境变量名
    base_url: Optional[str] = None
    timeout: float = 60.0

    def resolved(self) -> "NodeLLMConfig":
        """用 provider 默认值补全空字段，返回新对象。"""
        d = _PROVIDER_DEFAULTS.get(self.provider)
        if d is None:
            raise ValueError(f"未知 provider: {self.provider}（支持 anthropic/openai/mock）")
        return NodeLLMConfig(
            provider=self.provider,
            model=self.model or d["model"],
            system=self.system,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            api_key_env=self.api_key_env or d["api_key_env"],
            base_url=self.base_url or d["base_url"],
            timeout=self.timeout,
        )


def _http_post_json(url: str, headers: Dict[str, str], body: Dict[str, Any],
                    timeout: float) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:500]
        # 不回显 key：只暴露状态码与服务端返回体
        raise RuntimeError(f"LLM HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM 网络错误: {e.reason}") from None


def _require_key(cfg: NodeLLMConfig) -> str:
    key = os.environ.get(cfg.api_key_env or "")
    if not key:
        raise RuntimeError(
            f"provider={cfg.provider} 需要 API Key，但环境变量 {cfg.api_key_env} 未设置。"
        )
    return key


def _call_anthropic(cfg: NodeLLMConfig, prompt: str) -> str:
    """Claude Messages API。"""
    key = _require_key(cfg)
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body: Dict[str, Any] = {
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "temperature": cfg.temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if cfg.system:
        body["system"] = cfg.system
    resp = _http_post_json(cfg.base_url, headers, body, cfg.timeout)
    # content 是 block 列表，拼接所有 text 块
    parts = [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
    return "".join(parts).strip()


def _call_openai(cfg: NodeLLMConfig, prompt: str) -> str:
    """OpenAI Chat Completions API。"""
    key = _require_key(cfg)
    headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}
    messages = []
    if cfg.system:
        messages.append({"role": "system", "content": cfg.system})
    messages.append({"role": "user", "content": prompt})
    body = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    resp = _http_post_json(cfg.base_url, headers, body, cfg.timeout)
    return resp["choices"][0]["message"]["content"].strip()


def _call_mock(cfg: NodeLLMConfig, prompt: str) -> str:
    """离线桩：返回确定性文本，便于无 key 跑 demo / 测试。"""
    head = prompt.strip().splitlines()[0] if prompt.strip() else ""
    return f"[mock:{cfg.model}] 针对「{head[:40]}」的生成结果"


_DISPATCH = {"anthropic": _call_anthropic, "openai": _call_openai, "mock": _call_mock}


class LLMRegistry:
    """加载配置、按节点名解析 LLM 配置并执行补全。

    配置文件结构（JSON）：
        {
          "defaults": {"provider": "anthropic", "temperature": 0.3},
          "nodes": {
            "planner":  {"provider": "anthropic", "model": "claude-opus-4-8",
                         "system": "你是需求分析专家"},
            "coder":    {"provider": "openai",    "model": "gpt-4o"},
            "debugger": {"provider": "mock"}
          }
        }
    每个节点的最终配置 = provider 默认值 ← defaults ← 该节点 nodes[name]（后者优先）。
    """

    def __init__(self, defaults: Optional[Dict[str, Any]] = None,
                 nodes: Optional[Dict[str, Dict[str, Any]]] = None):
        self._defaults = defaults or {}
        self._nodes = nodes or {}

    @classmethod
    def from_file(cls, path: str) -> "LLMRegistry":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return cls(raw.get("defaults", {}), raw.get("nodes", {}))

    @classmethod
    def load(cls, path: Optional[str] = None) -> "LLMRegistry":
        """从文件加载；文件不存在则返回全 mock registry（demo 可离线跑）。"""
        path = path or os.environ.get("AGENTFLOW_LLM_CONFIG", "llm_config.json")
        if path and os.path.exists(path):
            return cls.from_file(path)
        return cls()  # 空配置 → 所有节点走 mock

    def config_for(self, node: str) -> NodeLLMConfig:
        merged: Dict[str, Any] = {}
        merged.update(self._defaults)
        merged.update(self._nodes.get(node, {}))
        # 只保留 NodeLLMConfig 的合法字段，忽略 _comment / _note 等注释键
        valid = {f.name for f in fields(NodeLLMConfig)}
        merged = {k: v for k, v in merged.items() if k in valid}
        if not merged:
            merged = {"provider": "mock"}
        return NodeLLMConfig(**merged).resolved()

    def complete(self, node: str, prompt: str, *, system: Optional[str] = None) -> str:
        """对指定节点执行一次补全。system 入参可临时覆盖配置里的 system。"""
        cfg = self.config_for(node)
        if system is not None:
            cfg.system = system
        return _DISPATCH[cfg.provider](cfg, prompt)
