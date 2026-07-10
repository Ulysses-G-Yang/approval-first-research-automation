from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class SettingsError(ValueError):
    pass


def default_config_path() -> Path:
    return Path.home() / "GenericCrawler" / "agent.yaml"


@dataclass
class ProviderConfig:
    name: str
    kind: str
    model: str
    secret_ref: str
    base_url: str = ""
    endpoint: str = ""
    timeout_seconds: float = 30.0

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "kind": self.kind,
            "model": self.model,
            "secret_ref": self.secret_ref,
            "timeout_seconds": self.timeout_seconds,
        }
        if self.base_url:
            data["base_url"] = self.base_url
        if self.endpoint:
            data["endpoint"] = self.endpoint
        return data

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "ProviderConfig":
        if "api_key" in data:
            raise SettingsError("Provider config must use secret_ref, not api_key.")
        kind = str(data.get("kind", "")).strip()
        if kind not in {"openai_compatible", "gemini", "qwen"}:
            raise SettingsError(f"Unsupported provider kind: {kind}")
        model = str(data.get("model", "")).strip()
        secret_ref = str(data.get("secret_ref", "")).strip()
        if not model or not secret_ref:
            raise SettingsError(f"Provider {name} requires model and secret_ref.")
        base_url = str(data.get("base_url", "")).strip()
        timeout_seconds = float(data.get("timeout_seconds", 30))
        if kind == "openai_compatible" and not base_url:
            raise SettingsError(f"Provider {name} requires base_url for openai_compatible mode.")
        if timeout_seconds <= 0:
            raise SettingsError(f"Provider {name} timeout_seconds must be greater than zero.")
        return cls(
            name=name,
            kind=kind,
            model=model,
            secret_ref=secret_ref,
            base_url=base_url,
            endpoint=str(data.get("endpoint", "")).strip(),
            timeout_seconds=timeout_seconds,
        )


@dataclass
class AgentSettings:
    default_provider: Optional[str] = None
    providers: Dict[str, ProviderConfig] = field(default_factory=dict)
    plugins: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "version": 1,
            "providers": {name: provider.to_dict() for name, provider in self.providers.items()},
            "plugins": self.plugins,
        }
        if self.default_provider:
            data["default_provider"] = self.default_provider
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentSettings":
        raw_providers = data.get("providers") or {}
        if not isinstance(raw_providers, dict):
            raise SettingsError("providers must be a mapping.")
        providers = {
            str(name): ProviderConfig.from_dict(str(name), dict(config))
            for name, config in raw_providers.items()
            if isinstance(config, dict)
        }
        default_provider = data.get("default_provider")
        if default_provider and default_provider not in providers:
            raise SettingsError("default_provider is not configured.")
        plugins = [str(name) for name in data.get("plugins") or []]
        return cls(default_provider=default_provider, providers=providers, plugins=plugins)


def load_settings(path: Optional[Path] = None) -> AgentSettings:
    target = path or default_config_path()
    if not target.exists():
        return AgentSettings()
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SettingsError("Agent config must be a YAML mapping.")
    return AgentSettings.from_dict(data)


def save_settings(settings: AgentSettings, path: Optional[Path] = None) -> Path:
    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(
        yaml.safe_dump(settings.to_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    temp.replace(target)
    return target
