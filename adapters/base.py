"""
平台适配器抽象基类。

定义了一类网站的共性：字段提取规则、分页策略、反检测配置，
以及采集后的数据处理逻辑。

子类化示例::

    class MySiteAdapter(PlatformAdapter):
        platform_name = "mysite"

        def match(self, url: str) -> bool:
            return "mysite.com" in url

        def get_item_selector(self) -> str:
            return ".item-card"

        def get_fields(self) -> list[ExtractionField]:
            return [
                ExtractionField(name="标题", description="商品标题",
                                selector="h2.title",
                                validation={"non_empty": {}, "min_length": 2}),
            ]
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ExtractionField:
    """字段提取定义。

    封装了一个字段的完整元信息：从哪里取、怎么取、如何验证。
    相比原始 YAML 的 ``{name, selector, attr}``，增加了：
    - ``description``：供 LLM 理解字段含义
    - ``fallback_selectors``：备用选择器列表
    - ``validation``：质量校验规则
    """

    name: str
    """字段名称（中文或英文均可）"""
    description: str
    """字段语义描述（供 LLM 理解字段含义，如 "商品当前售价，不含运费"）"""
    selector: str
    """主 CSS/XPath 选择器"""
    attr: Optional[str] = None
    """提取属性名（如 ``href``、``src``），None 表示提取 inner_text"""
    fallback_selectors: List[str] = field(default_factory=list)
    """备用选择器列表，按顺序尝试"""
    validation: Optional[Dict[str, Any]] = None
    """质量校验规则，见 ``core.quality_gate.QualityGate`` 支持的规则"""


class PlatformAdapter(ABC):
    """平台适配器抽象基类。

    每个子类封装**一类网站**的共性，而不是一个具体网站。
    例如 ``ECommerceAdapter`` 封装了电商平台的通用字段
    （商品标题、价格、图片），不绑定某个具体域名。

    GenericSpider 可以通过 ``adapter.to_config(start_url)``
    获取兼容的配置字典，从而无需手写 YAML。
    """

    platform_name: str = "generic"
    """平台类名（用于日志和标识）"""
    platform_version: str = "1.0"
    """适配器版本号"""

    # ── 子类必须实现的方法 ──────────────────────────────────────

    @abstractmethod
    def match(self, url: str) -> bool:
        """判断此适配器是否匹配给定 URL。

        实现时通常检查域名是否在已知列表中。

        Args:
            url: 目标页面 URL。

        Returns:
            True 表示此适配器可以处理该 URL。
        """
        ...

    @abstractmethod
    def get_item_selector(self) -> str:
        """获取列表项容器 CSS 选择器。

        对应 YAML 中的 ``item_selector`` 字段。
        如果页面是单条详情页（无列表），返回空字符串。
        """
        ...

    @abstractmethod
    def get_fields(self) -> List[ExtractionField]:
        """获取字段提取定义列表。

        返回的字段顺序即采集时的处理顺序。
        """
        ...

    # ── 子类可选覆盖的方法 ──────────────────────────────────────

    def get_pagination_strategy(self) -> Dict[str, Any]:
        """获取分页策略。

        默认不做分页。子类可以覆盖以支持翻页。

        Returns:
            与 GenericSpider 兼容的分页配置字典：
            ``{"enabled": True, "max_pages": 10, "next_selector": ".next a", "delay_ms": 1000}``
        """
        return {"enabled": False, "max_pages": 1}

    def get_stealth_profile(self) -> Dict[str, Any]:
        """获取反检测配置。

        Returns:
            ``{"stealth": True}`` 或 ``{"stealth": False}``。
            未来可扩展为梯度策略配置。
        """
        return {"stealth": False}

    def get_pre_extract_actions(self) -> List[Dict[str, Any]]:
        """获取页面加载后、字段提取前需要执行的动作。

        例如：滚动到底部触发懒加载、等待某个元素出现。

        Returns:
            动作列表，每项为 ``{"type": "scroll", "times": 3}`` 或
            ``{"type": "wait", "selector": ".loaded"}``。
        """
        return []

    def post_process(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """采集后处理钩子。

        可用于：价格格式化、日期标准化、字段重命名、空值填充等。

        Args:
            records: 原始采集结果列表。

        Returns:
            处理后的结果列表。
        """
        return records

    # ── 序列化方法 ──────────────────────────────────────────────

    def to_config(self, start_url: str, **overrides: Any) -> Dict[str, Any]:
        """将适配器导出为 GenericSpider 兼容的配置字典。

        Args:
            start_url: 起始 URL。
            **overrides: 可覆盖任意配置项（如 ``headless=True``）。

        Returns:
            可直接传给 ``GenericSpider(config)`` 的字典。
        """
        browser_defaults: Dict[str, Any] = {"headless": True}
        browser_defaults.update(self.get_stealth_profile())

        config: Dict[str, Any] = {
            "name": self.platform_name,
            "start_url": start_url,
            "enable_adaptive": True,
            "browser": browser_defaults,
            "pagination": self.get_pagination_strategy(),
            "item_selector": self.get_item_selector(),
            "fields": [
                {
                    "name": field.name,
                    "selector": field.selector,
                    "attr": field.attr,
                    "description": field.description,
                }
                for field in self.get_fields()
            ],
        }

        # 允许调用方覆盖任意配置
        config.update(overrides)
        return config
