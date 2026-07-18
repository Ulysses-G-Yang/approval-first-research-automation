"""
电商平台通用适配器。

封装了电商平台的共性字段：
- 商品标题
- 价格
- 商品图片
- 卖家/店铺信息（可选）
- 评分（可选）

支持域名：taobao.com, tmall.com, jd.com, pinduoduo.com,
          amazon.com, ebay.com, shopee.com, 等。
"""

from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urlparse

from .base import ExtractionField, PlatformAdapter


class ECommerceAdapter(PlatformAdapter):
    """电商平台通用适配器。

    自动匹配主流电商域名，提供通用的商品字段提取规则。
    适用于商品列表页和详情页。
    """

    platform_name = "ecommerce"
    platform_version = "1.0"

    # 支持的电商域名（子域名自动匹配）
    ECOMMERCE_DOMAINS: List[str] = [
        "taobao.com",
        "tmall.com",
        "jd.com",
        "pinduoduo.com",
        "yangkeduo.com",  # 拼多多移动端
        "amazon.com",
        "amazon.cn",
        "ebay.com",
        "shopee.com",
        "shopee.cn",
        "lazada.com",
        "1688.com",
        "aliexpress.com",
        "suning.com",
        "gome.com.cn",
        "vip.com",
        "mogujie.com",
        "yanxuan.com",  # 网易严选
        "dangdang.com",
    ]

    # ── PlatformAdapter 实现 ─────────────────────────────────────

    def match(self, url: str) -> bool:
        try:
            domain = urlparse(url).netloc.lower()
        except Exception:
            return False
        return any(d in domain for d in self.ECOMMERCE_DOMAINS)

    def get_item_selector(self) -> str:
        # 覆盖大部分电商列表页的通用选择器
        return (
            "[class*='item'], [class*='Item'], "
            "[class*='product'], [class*='Product'], "
            "[class*='card'], [class*='Card'], "
            "[class*='goods'], [class*='Goods'], "
            "[data-component*='item'], [data-component*='product'], "
            "li"
        )

    def get_fields(self) -> List[ExtractionField]:
        return [
            ExtractionField(
                name="商品标题",
                description="商品名称或标题",
                selector=(
                    "h1, h2, h3, "
                    "[class*='title'], [class*='Title'], "
                    "[class*='name'], [class*='Name'], "
                    "[class*='product-title'], [class*='productName'], "
                    "[data-field='title']"
                ),
                fallback_selectors=[
                    "[class*='product-name']",
                    "[class*='item-title']",
                    "[class*='goods-name']",
                ],
                validation={"non_empty": {}, "min_length": 2, "max_length": 500},
            ),
            ExtractionField(
                name="价格",
                description="商品当前售价（取页面显示价格，不含运费）",
                selector=(
                    "[class*='price'], [class*='Price'], "
                    "[class*='amount'], [class*='Amount'], "
                    "[class*='current-price'], [class*='salePrice'], "
                    "span:has-text('¥'), span:has-text('￥'), "
                    "span:has-text('$'), span:has-text('€')"
                ),
                fallback_selectors=[
                    "[data-field='price']",
                    "[class*='goods-price']",
                    "[class*='product-price']",
                ],
                validation={"type": "price"},
            ),
            ExtractionField(
                name="商品图片",
                description="商品主图 URL",
                selector=(
                    "img[class*='main'], img[class*='primary'], "
                    "img[class*='cover'], img[class*='thumb'], "
                    "img[src*='product'], img[src*='goods'], "
                    "img[src*='item']"
                ),
                attr="src",
                fallback_selectors=["img:first-child", "img"],
                validation={"type": "url"},
            ),
            ExtractionField(
                name="商品链接",
                description="商品详情页 URL",
                selector="a[href*='item'], a[href*='product'], a[href*='goods'], a[href*='detail']",
                attr="href",
                fallback_selectors=["a[class*='title']", "a[class*='name']"],
                validation={"type": "url"},
            ),
            ExtractionField(
                name="店铺名称",
                description="卖家或店铺名称",
                selector=(
                    "[class*='shop'], [class*='Shop'], "
                    "[class*='seller'], [class*='Seller'], "
                    "[class*='store'], [class*='Store'], "
                    "[class*='brand'], [class*='Brand'], "
                    "[class*='nick'], [class*='Nick']"
                ),
                fallback_selectors=[
                    "[data-field='shopName']",
                    "[data-field='sellerNick']",
                ],
                validation={"min_length": 1, "max_length": 200},
            ),
            ExtractionField(
                name="评分",
                description="商品评分或好评率",
                selector=(
                    "[class*='rating'], [class*='Rating'], "
                    "[class*='score'], [class*='Score'], "
                    "[class*='star'], [class*='Star'], "
                    "[class*='review-score']"
                ),
                fallback_selectors=[
                    "[data-field='rating']",
                    "[class*='product-score']",
                ],
                validation={"regex": {"pattern": r"[\d.]+"}},
            ),
        ]

    def get_pagination_strategy(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "max_pages": 10,
            "next_selector": (
                "[class*='next'] a, "
                "[class*='Next'] a, "
                "a[rel='next'], "
                "[class*='pagination'] [class*='next'], "
                ".pagination .next, "
                ".pager .next"
            ),
            "delay_ms": 1500,
        }

    def get_stealth_profile(self) -> Dict[str, Any]:
        # 电商网站通常有较强的反爬措施
        return {"stealth": True}

    def post_process(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """清洗电商数据：价格标准化、空值处理。"""
        import re

        for record in records:
            # 价格清洗：提取首个数字
            raw_price = record.get("价格", "")
            if raw_price:
                price_match = re.search(r"[\d.]+", str(raw_price))
                if price_match:
                    try:
                        record["价格"] = float(price_match.group())
                    except ValueError:
                        pass

            # 评分清洗
            raw_rating = record.get("评分", "")
            if raw_rating:
                rating_match = re.search(r"[\d.]+", str(raw_rating))
                if rating_match:
                    try:
                        record["评分"] = float(rating_match.group())
                    except ValueError:
                        pass

        return records
