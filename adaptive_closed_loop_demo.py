#!/usr/bin/env python3
"""
自适应闭环采集 Demo —— 最小可运行版本。

演示完整链路：
  页面改版 -> 选择器失效 -> LLM 修复 -> 质量校验 -> 自动重试验证 -> 持久化修复记忆

启动方式：
  python adaptive_closed_loop_demo.py

依赖：
  - Python 3.12+
  - pip install playwright  （Demo 使用模拟页面，不需要真实浏览器）
  - ollama pull qwen3       （可选，没有 Ollama 时自动跳过 LLM 修复层）

注意：
  Windows 控制台如遇编码问题，请执行：
    set PYTHONIOENCODING=utf-8 && python adaptive_closed_loop_demo.py

架构：
  本 Demo 独立于项目的 agent CLI 和 Playwright 浏览器，
  只依赖 core/ 下的新模块：
    core/quality_gate.py       — 字段级质量校验
    core/repair_persistence.py — 修复记忆持久化
    core/self_healing.py       — 自适应闭环引擎

场景设计：
  - 场景 A：页面 v1（原始结构），选择器正常命中。
  - 场景 B：页面 v2（模拟改版后 class 全变了），选择器失效，
            引擎走完 L1→L2→L3→L4→L5 全链路，LLM 修复成功后持久化。
  - 场景 C：再次访问 v2（同一模式不同 ID），修复记忆命中（L3），
            跳过 LLM 调用，直接返回缓存的选择器。
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# 确保项目根目录在 sys.path 中
_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from core.quality_gate import QualityGate
from core.repair_persistence import RepairPersistence
from core.self_healing import HealingResult, SelfHealingEngine


# ═══════════════════════════════════════════════════════════════════
# 模拟页面（不需要 Playwright）
# ═══════════════════════════════════════════════════════════════════

class MockElement:
    """模拟 Playwright ElementHandle。"""

    def __init__(self, text: str, attributes: Optional[Dict[str, str]] = None):
        self.text = text
        self.attrs = attributes or {}

    async def inner_text(self) -> str:
        return self.text

    async def get_attribute(self, name: str) -> str:
        return self.attrs.get(name, "")


class MockPage:
    """模拟网页，v1 和 v2 结构不同。

    v1: class="product-card" > h2.title + span.price
    v2: data-component="product-hero" > h1.product-name + div.current-price
    """

    def __init__(self, version: int = 1, item_id: int = 1):
        self.version = version
        self.item_id = item_id
        self.url = f"https://demo-shop.local/products/{item_id}"

    def get_html(self) -> str:
        if self.version == 1:
            return f"""<html><body>
              <div class="product-card">
                <h2 class="title">Northwind 笔记本 {self.item_id}</h2>
                <span class="price">¥{39.90 + self.item_id * 0.1:.2f}</span>
                <span class="rating">4.8</span>
              </div>
            </body></html>"""
        else:
            # v2: 结构改版 — class 全部重命名
            return f"""<html><body>
              <div data-component="product-hero">
                <h1 class="product-name">Northwind 笔记本 {self.item_id}</h1>
                <div class="current-price">¥{39.90 + self.item_id * 0.1:.2f}</div>
                <span class="star-rating">4.8</span>
              </div>
            </body></html>"""

    async def query_selector(self, selector: str):
        """模拟 CSS 选择器查询（支持简单 class 选择器和属性选择器）。"""
        # v1 selectors
        if self.version == 1:
            v1_map = {
                ".title": f"Northwind 笔记本 {self.item_id}",
                ".price": f"¥{39.90 + self.item_id * 0.1:.2f}",
                ".rating": "4.8",
            }
            for sel, val in v1_map.items():
                if self._match(selector, sel):
                    return MockElement(val)

        # v2 selectors
        if self.version >= 2:
            v2_map = {
                ".product-name": f"Northwind 笔记本 {self.item_id}",
                ".current-price": f"¥{39.90 + self.item_id * 0.1:.2f}",
                ".star-rating": "4.8",
            }
            for sel, val in v2_map.items():
                if self._match(selector, sel):
                    return MockElement(val)

        return None

    def _match(self, selector: str, target: str) -> bool:
        """Simple CSS selector match supporting class and attribute selectors."""
        # Exact match
        if selector == target:
            return True
        # Attribute-based: [class*='foo']
        import re
        attr_match = re.match(r"\[class\*\s*=\s*['\"]([^'\"]+)['\"]\]", selector)
        if attr_match:
            partial = attr_match.group(1)
            # Extract class name from target like .product-name -> product-name
            if target.startswith("."):
                return partial in target.lstrip(".")
        return False

    async def content(self) -> str:
        return self.get_html()


# ═══════════════════════════════════════════════════════════════════
# Demo 字段定义（兼容 dict 格式，作为 ExtractionField 的替代）
# ═══════════════════════════════════════════════════════════════════

@dataclass
class DemoField:
    name: str
    description: str
    selector: str
    attr: Optional[str] = None
    fallback_selectors: List[str] = field(default_factory=list)
    validation: Optional[Dict[str, Any]] = None


# ═══════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════

def print_result(method: str, name: str, value: str, confidence: float) -> None:
    """统一格式化输出提取结果。"""
    icons = {
        "configured": "L1",
        "fallback": "L2",
        "cached_repair": "L3",
        "scrapling_adaptive": "L4",
        "llm_text": "L5",
        "exhausted": "!!",
    }
    icon = icons.get(method, "??")
    status = "[OK]" if value else "[FAIL]"
    print(f"  [{icon}] {status} {name} = {value or '(空)'} (置信度 {confidence:.0%})")


# ═══════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════

async def demo():
    print("=" * 60)
    print("  自适应闭环采集 Demo")
    print("  演示: 页面改版 -> 选择器失效 -> 修复 -> 验证 -> 固化")
    print("=" * 60)

    # 初始化引擎
    engine = SelfHealingEngine(
        enable_llm=True,
        llm_model="qwen3",
        enable_scrapling=False,  # Demo 不依赖 Scrapling
        repair_db_path=str(Path(__file__).parent / ".demo_repairs.jsonl"),
    )

    # 定义采集字段（模拟 ExtractionField）
    fields = [
        DemoField(
            name="商品标题",
            description="商品名称或标题",
            selector=".title",  # ← 这个选择器在 v2 中失效
            fallback_selectors=["[class*='product-name']", "h2"],
            validation={"non_empty": {}, "min_length": 2, "max_length": 200},
        ),
        DemoField(
            name="价格",
            description="商品当前售价",
            selector=".price",  # ← 这个选择器在 v2 中失效
            fallback_selectors=["[class*='price']", "span:has-text('¥')"],
            validation={"type": "price"},
        ),
        DemoField(
            name="评分",
            description="商品评分或好评率",
            selector=".rating",
            fallback_selectors=["[class*='rating']", "[class*='star']"],
            validation={"regex": {"pattern": r"\d+\.?\d*"}},
        ),
    ]

    all_results: List[List[HealingResult]] = []

    # ── 场景 A: v1 正常采集 ──────────────────────────────────────
    print("\n[*] 场景 A: 页面 v1 (原始结构) -- 3 个商品")
    print("-" * 40)
    for item_id in range(1, 4):
        page = MockPage(version=1, item_id=item_id)
        print(f"\n  商品 #{item_id} ({page.url}):")
        results: List[HealingResult] = []
        for f in fields:
            result = await engine.extract_with_healing(page, f)
            print_result(result.method, f.name, result.value, result.confidence)
            results.append(result)
        all_results.append(results)

    # ── 场景 B: v2 结构变更 ──────────────────────────────────────
    print("\n" + "=" * 60)
    print("[WARN] 模拟网站改版：class 名全部更新")
    print("=" * 60)
    print("\n[*] 场景 B: 页面 v2 (结构变更) -- 3 个新商品")
    print("-" * 40)
    for item_id in range(4, 7):
        page = MockPage(version=2, item_id=item_id)
        print(f"\n  商品 #{item_id} ({page.url}):")
        results: List[HealingResult] = []
        for f in fields:
            result = await engine.extract_with_healing(page, f)
            print_result(result.method, f.name, result.value, result.confidence)
            results.append(result)
        all_results.append(results)

    # --- 场景 C: 再次访问 v2（验证修复记忆） ---
    print("\n" + "=" * 60)
    print("[>>] 验证：再次访问同模式页面（不同商品ID）")
    print("     预期：修复记忆命中（L3），不再调用 LLM")
    print("=" * 60)
    print("\n[*] 场景 C: 再次访问 v2 -- 3 个新商品")
    print("-" * 40)
    for item_id in range(7, 10):
        page = MockPage(version=2, item_id=item_id)
        print(f"\n  商品 #{item_id} ({page.url}):")
        results: List[HealingResult] = []
        for f in fields:
            result = await engine.extract_with_healing(page, f)
            print_result(result.method, f.name, result.value, result.confidence)
            results.append(result)
        all_results.append(results)

    # --- 总结 ---
    print("\n" + "=" * 60)
    print("  Demo 完成 -- 统计报告")
    print("=" * 60)

    total = sum(len(r) for r in all_results)
    successful = sum(1 for r in all_results for item in r if item.validated)
    print(f"\n  总字段数: {total}")
    print(f"  成功提取: {successful}")
    print(f"  成功率:   {successful/max(total,1)*100:.0f}%")

    stats = engine.repair_memory.stats()
    print(f"\n  [==] 修复记忆库:")
    print(f"     总修复记录: {stats['total']}")
    print(f"     成功修复:   {stats['success']}")
    print(f"     成功率:     {stats['rate']*100:.0f}%")

    # 验证 Demo 语义
    scene_a_ok = all(r.validated for r in all_results[0])
    scene_b_l5 = any(r.method == "llm_text" for r in all_results[3])  # 场景 B 第一个商品
    scene_c_l3 = any(r.method == "cached_repair" for r in all_results[6])  # 场景 C 第一个商品

    print(f"\n  [??] 语义验证:")
    print(f"     场景 A 全部配置命中: {'[OK]' if scene_a_ok else '[FAIL]'}")
    print(f"     场景 B 触发了 LLM 修复: {'[OK]' if scene_b_l5 else '[N/A] (可能 Ollama 未运行)'}")
    print(f"     场景 C 修复记忆命中: {'[OK] 闭环成功！' if scene_c_l3 else '[N/A] (如果LLM未运行属正常)'}")

    # 清理 Demo 临时文件
    demo_db = Path(engine.repair_memory.db_path)
    if demo_db.exists():
        demo_db.unlink()
        print(f"\n  [--] 已清理临时文件: {demo_db.name}")

    print("\n" + "=" * 60)
    if successful == total:
        print("[OK] 所有字段提取成功！自适应闭环工作正常。")
    else:
        print(f"[WARN] {total - successful} 个字段未能提取，请检查 LLM 是否可用。")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(demo())
