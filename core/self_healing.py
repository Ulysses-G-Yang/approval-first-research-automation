"""
自适应闭环引擎 (Self-Healing Engine)。

集成质量校验、修复持久化与 LLM 修复能力，实现 5 层退化链路：

1. **Layer 1** — 配置选择器 (Playwright 原生查询)
2. **Layer 2** — 备用选择器列表 (fallback_selectors)
3. **Layer 3** — 修复记忆库查询 (RepairPersistence.suggest)
4. **Layer 4** — Scrapling 自适应解析
5. **Layer 5** — LLM 选择器修复（需显式启用）

每层提取成功后都经过 QualityGate 校验，校验通过的修复结果
自动持久化到 RepairMemory，下次遇到同样字段失效时可跳过 LLM 调用。

设计原则：
- 与 ``GenericSpider`` 解耦：可以独立使用，也可嵌入现有引擎
- LLM 修复默认关闭（省钱 + 安全）
- 每层都有独立的日志输出，方便调试
- 全部失败时返回空值 + 日志，不中断整页采集
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.quality_gate import QualityGate
from core.repair_persistence import RepairPersistence

logger = logging.getLogger(__name__)


@dataclass
class HealingResult:
    """单次闭环提取的结果。"""

    selector: str
    """最终使用的选择器（或原始选择器如果全部失败）"""
    value: str
    """提取到的值（空字符串表示失败）"""
    confidence: float
    """置信度 0.0 ~ 1.0：
    - 1.0: 配置选择器直接命中
    - 0.9: 备用选择器命中
    - 0.85: 修复记忆命中
    - 0.7: Scrapling 自适应命中
    - 0.75: LLM 文本修复命中
    - 0.0: 全部失败
    """
    method: str
    """命中的层级：
    ``"configured"`` | ``"fallback"`` | ``"cached_repair"`` |
    ``"scrapling_adaptive"`` | ``"llm_text"`` | ``"exhausted"``
    """
    validated: bool
    """是否通过质量校验"""


# Scrapling 可选导入
try:
    from scrapling.parser import Selector as ScraplingSelector
except Exception:  # pragma: no cover
    try:
        from scrapling import Selector as ScraplingSelector
    except Exception:  # pragma: no cover
        ScraplingSelector = None  # type: ignore[assignment]


class SelfHealingEngine:
    """自适应闭环引擎。

    用法::

        engine = SelfHealingEngine(enable_llm=True, llm_model="qwen3")
        result = await engine.extract_with_healing(page, field)

    Args:
        enable_llm: 是否启用 LLM 修复（默认 False，省钱）。
        llm_model: LLM 模型名（默认 ``qwen3``，用于 Ollama 本地调用）。
        enable_scrapling: 是否启用 Scrapling 自适应层（默认 True）。
        repair_db_path: 修复记忆库文件路径。
    """

    def __init__(
        self,
        *,
        enable_llm: bool = False,
        llm_model: str = "qwen3",
        enable_scrapling: bool = True,
        repair_db_path: str = "~/.generic_crawler/repairs.jsonl",
    ):
        self.enable_llm = enable_llm
        self.llm_model = llm_model
        self.enable_scrapling = enable_scrapling and ScraplingSelector is not None
        self.quality_gate = QualityGate()
        self.repair_memory = RepairPersistence(repair_db_path)

        # LLM 修复器延迟初始化
        self._llm_repairer = None

    # ── 公共入口 ──────────────────────────────────────────────────

    async def extract_with_healing(
        self,
        page: Any,
        field: Any,
        *,
        context_node: Any = None,
    ) -> HealingResult:
        """对单个字段执行带闭环修复的提取。

        Args:
            page: Playwright Page 对象（或任何有 ``query_selector`` + ``content`` 的对象）。
            field: 字段定义。可以是 ``ExtractionField`` 或兼容的 dict
                   （至少包含 ``name``, ``selector``, ``attr``, ``description``,
                   ``fallback_selectors``, ``validation``）。
            context_node: 可选的限定节点（Playwright ElementHandle），
                          为 None 时从整页提取。

        Returns:
            HealingResult: 提取结果。
        """
        # 兼容 dict 和 dataclass
        field_name = _get_attr(field, "name", "")
        field_selector = _get_attr(field, "selector", "")
        field_attr = _get_attr(field, "attr", None)
        field_description = _get_attr(field, "description", "") or field_name
        field_validation = _get_attr(field, "validation", None)
        fallback_selectors = _get_attr(field, "fallback_selectors", []) or []

        page_url = _safe_url(page)

        # ── Layer 1: 配置选择器 ──────────────────────────────────
        value = await _try_playwright_selector(
            context_node or page, field_selector, field_attr
        )
        if value and self.quality_gate.validate(value, field_validation).passed:
            logger.debug("L1 configured: %s → %s", field_selector, _truncate(value))
            return HealingResult(
                selector=field_selector,
                value=value,
                confidence=1.0,
                method="configured",
                validated=True,
            )
        if value:
            logger.debug(
                "L1 extracted but validation failed: %s → %s", field_name, _truncate(value)
            )

        # ── Layer 2: 备用选择器 ──────────────────────────────────
        for fb_selector in fallback_selectors:
            value = await _try_playwright_selector(
                context_node or page, fb_selector, field_attr
            )
            if value and self.quality_gate.validate(value, field_validation).passed:
                logger.info("L2 fallback: %s → %s", fb_selector, _truncate(value))
                return HealingResult(
                    selector=fb_selector,
                    value=value,
                    confidence=0.9,
                    method="fallback",
                    validated=True,
                )

        # ── Layer 3: 修复记忆库 ──────────────────────────────────
        cached = self.repair_memory.suggest(field_name, page_url)
        if cached:
            value = await _try_playwright_selector(
                context_node or page, cached, field_attr
            )
            if value and self.quality_gate.validate(value, field_validation).passed:
                logger.info("L3 cached repair: %s → %s", cached, _truncate(value))
                return HealingResult(
                    selector=cached,
                    value=value,
                    confidence=0.85,
                    method="cached_repair",
                    validated=True,
                )

        # ── Layer 4: Scrapling 自适应 ────────────────────────────
        if self.enable_scrapling:
            value = await self._try_scrapling(page, field_selector, field_name, context_node)
            if value and self.quality_gate.validate(value, field_validation).passed:
                logger.info("L4 scrapling adaptive: %s → %s", field_selector, _truncate(value))
                return HealingResult(
                    selector=field_selector,  # Scrapling 基于原选择器自适应
                    value=value,
                    confidence=0.7,
                    method="scrapling_adaptive",
                    validated=True,
                )

        # ── Layer 5: LLM 修复 ────────────────────────────────────
        if self.enable_llm:
            logger.info("L5 invoking LLM repair for %s", field_name)
            html = await _safe_content(page, context_node)
            new_selector = await self._llm_repair(
                field_name=field_name,
                field_description=field_description,
                failed_selector=field_selector,
                page_html=html,
            )
            if new_selector and new_selector != field_selector:
                value = await _try_playwright_selector(
                    context_node or page, new_selector, field_attr
                )
                validated = bool(value) and self.quality_gate.validate(
                    value, field_validation
                ).passed

                # 记录修复结果
                self.repair_memory.record(
                    field_name=field_name,
                    old_selector=field_selector,
                    new_selector=new_selector,
                    page_url=page_url,
                    success=validated,
                )

                if validated:
                    logger.info(
                        "L5 LLM repaired: %s → %s → %s",
                        field_name,
                        new_selector,
                        _truncate(value),
                    )
                    return HealingResult(
                        selector=new_selector,
                        value=value,
                        confidence=0.75,
                        method="llm_text",
                        validated=True,
                    )
                logger.warning(
                    "L5 LLM returned selector but validation failed: %s → %s",
                    field_name,
                    new_selector,
                )

        # ── 全部失败 ──────────────────────────────────────────────
        logger.warning(
            "All layers exhausted for field=%s selector=%s", field_name, field_selector
        )
        return HealingResult(
            selector=field_selector,
            value="",
            confidence=0.0,
            method="exhausted",
            validated=False,
        )

    # ── 内部方法 ──────────────────────────────────────────────────

    async def _try_scrapling(
        self,
        page: Any,
        selector: str,
        identifier: str,
        context_node: Any = None,
    ) -> str:
        """尝试用 Scrapling 自适应模式提取。"""
        if ScraplingSelector is None:
            return ""

        try:
            html = await _safe_content(page, context_node, wrap=(context_node is not None))
        except Exception as exc:
            logger.debug("Scrapling HTML read failed: %s", exc)
            return ""

        if not html:
            return ""

        url = _safe_url(page)
        try:
            scrapling_obj = ScraplingSelector(html, adaptive=True, url=url or "default")
        except TypeError:
            try:
                scrapling_obj = ScraplingSelector(html, adaptive=True, url=url or "default")
            except Exception as exc:
                logger.debug("Scrapling init failed: %s", exc)
                return ""
        except Exception as exc:
            logger.debug("Scrapling init failed: %s", exc)
            return ""

        css_method = getattr(scrapling_obj, "css", None)
        if not callable(css_method):
            return ""

        try:
            kwargs: Dict[str, Any] = {}
            if _accepts_kwarg(css_method, "identifier"):
                kwargs["identifier"] = identifier
            if _accepts_kwarg(css_method, "adaptive"):
                kwargs["adaptive"] = True
            result = css_method(selector, **kwargs)
        except TypeError:
            result = css_method(selector)
        except Exception as exc:
            logger.debug("Scrapling css() failed: %s", exc)
            return ""

        return _resolve_scrapling_text(result)

    async def _llm_repair(
        self,
        field_name: str,
        field_description: str,
        failed_selector: str,
        page_html: str,
    ) -> str:
        """调用 LLM 生成新选择器。"""
        if self._llm_repairer is None:
            self._llm_repairer = _LLMRepairer(self.llm_model)
        return await self._llm_repairer.repair(
            field_description, failed_selector, page_html
        )


# ═══════════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════════

def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    """兼容 dict 和对象属性访问。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _safe_url(page: Any) -> str:
    try:
        return str(getattr(page, "url", ""))
    except Exception:
        return ""


async def _safe_content(page: Any, node: Any = None, wrap: bool = False) -> str:
    """安全获取页面或节点的 HTML 内容。"""
    target = node or page
    try:
        if node is not None and hasattr(node, "inner_html"):
            html = await node.inner_html()
        else:
            html = await page.content()
    except Exception:
        return ""

    if wrap and html.strip():
        html = f"<div>{html}</div>"

    return html or ""


async def _try_playwright_selector(
    target: Any,
    selector: str,
    attr: Optional[str],
) -> str:
    """通过 Playwright 选择器提取文本或属性。"""
    if target is None or not selector:
        return ""
    try:
        element = await target.query_selector(selector)
    except Exception:
        return ""
    if element is None:
        return ""
    try:
        if attr:
            value = await element.get_attribute(attr)
        else:
            value = await element.inner_text()
    except Exception:
        return ""
    return (value or "").strip()


def _truncate(text: str, max_len: int = 60) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _accepts_kwarg(func: Any, kwarg: str) -> bool:
    """检查函数是否接受指定关键字参数。"""
    import inspect

    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    return kwarg in sig.parameters or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )


def _resolve_scrapling_text(result: Any) -> str:
    """从 Scrapling 结果中提取文本。"""
    if result is None:
        return ""

    if isinstance(result, (str, bytes)):
        return str(result).strip()

    if isinstance(result, dict):
        return str(result.get("text") or result.get("data") or "").strip()

    nodes = []
    if hasattr(result, "first"):
        first = getattr(result, "first")
        if first is not None:
            nodes = [first]
    if not nodes:
        try:
            nodes = list(result)
        except TypeError:
            nodes = [result]

    for item in nodes:
        if item is None:
            continue
        if isinstance(item, (str, bytes)):
            return str(item).strip()
        for attr_name in ("text", "get", "text_content"):
            value = getattr(item, attr_name, None)
            if value is None:
                continue
            if callable(value):
                try:
                    text = str(value())
                except Exception:
                    continue
            else:
                text = str(value)
            if text.strip():
                return text.strip()

    return ""


# ═══════════════════════════════════════════════════════════════════
# LLM 修复器（轻量内部类）
# ═══════════════════════════════════════════════════════════════════

import asyncio
import re
import subprocess


class _LLMRepairer:
    """通过 Ollama 本地模型生成 CSS 选择器。"""

    def __init__(self, model: str = "qwen3"):
        self.model = model
        self._available: Optional[bool] = None

    def _check(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self._available = self.model in result.stdout
        except Exception:
            self._available = False
        return self._available

    async def repair(
        self,
        field_description: str,
        failed_selector: str,
        page_html: str,
    ) -> str:
        if not self._check():
            logger.debug("Ollama model %s not available, skipping LLM repair", self.model)
            return ""

        truncated = page_html.strip()[:3000]
        if len(page_html) > 3000:
            truncated += "\n...[truncated]..."

        prompt = (
            f"当前网页片段如下：\n{truncated}\n"
            f"我需要提取「{field_description}」的内容，"
            f"之前的选择器「{failed_selector}」已失效，"
            "请给出一个最精准的新CSS选择器，只输出选择器本身，不要解释。"
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                "ollama", "run", self.model, prompt,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=15
            )
        except asyncio.TimeoutError:
            logger.warning("LLM repair timed out for %s", field_description)
            return ""
        except Exception as exc:
            logger.warning("LLM repair failed: %s", exc)
            return ""

        raw = stdout.decode("utf-8", errors="replace").strip()
        return _clean_selector(raw)


def _clean_selector(raw: str) -> str:
    """清洗 LLM 输出的选择器字符串。"""
    if not raw:
        return ""

    # 去掉代码块标记
    fenced = re.search(r"```(?:css)?\s*(.*?)```", raw, flags=re.S | re.I)
    if fenced:
        raw = fenced.group(1).strip()

    # 取第一行有效内容
    for line in raw.splitlines():
        candidate = line.strip().strip("`").strip("'\"").rstrip(";")
        if candidate.lower().startswith("css") and ":" in candidate:
            candidate = candidate.split(":", 1)[1].strip()
        if not candidate:
            continue
        if len(candidate) > 500:
            continue
        if not re.fullmatch(
            r"[A-Za-z0-9_#.[\]=\"'():>+~*^$|,\\\-\s]+", candidate
        ):
            continue
        return candidate

    return ""
