"""
平台适配层 (Platform Adapter Layer)。

封装一类网站的共同特征，让"接入新网站"从"手写完整 YAML"变为"选择适配器 + 微调字段"。

使用方式::

    from adapters import ECommerceAdapter, get_adapter_for_url

    adapter = get_adapter_for_url("https://item.jd.com/123456.html")
    if adapter:
        config = adapter.to_config("https://item.jd.com/123456.html")
        # 将 config 传给 GenericSpider
"""

from __future__ import annotations

from .base import ExtractionField, PlatformAdapter
from .ecommerce import ECommerceAdapter

__all__ = [
    "ExtractionField",
    "ECommerceAdapter",
    "PlatformAdapter",
    "get_adapter_for_url",
    "list_adapters",
]

# 所有已注册的适配器类（按优先级排列）
_REGISTERED_ADAPTERS = [ECommerceAdapter]


def get_adapter_for_url(url: str) -> PlatformAdapter | None:
    """根据 URL 自动匹配最合适的平台适配器。

    Args:
        url: 目标页面 URL。

    Returns:
        匹配的适配器实例，无匹配时返回 None。
    """
    for adapter_cls in _REGISTERED_ADAPTERS:
        adapter = adapter_cls()
        if adapter.match(url):
            return adapter
    return None


def list_adapters() -> list[dict]:
    """列出所有已注册的平台适配器及其支持的域名。"""
    result = []
    for adapter_cls in _REGISTERED_ADAPTERS:
        instance = adapter_cls()
        result.append(
            {
                "name": instance.platform_name,
                "version": instance.platform_version,
                "domains": getattr(instance, "ECOMMERCE_DOMAINS", [])
                or getattr(instance, "_match_domains", []),
            }
        )
    return result
