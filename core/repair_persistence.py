"""
修复持久化模块。

将成功的 CSS 选择器修复记录到本地 JSONL 文件，支持：
- 记录修复历史（旧选择器 → 新选择器、页面模式、字段名）
- 按字段名 + 页面模式查询高置信度替代选择器
- 修复统计（总次数、成功率）

设计原则：
- 纯本地文件存储，不依赖数据库
- JSONL 格式方便追加写入和逐行查询
- 路径安全：所有文件操作限制在用户目录下
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RepairPersistence:
    """修复记忆库 —— 记录和查询历史 CSS 选择器修复。

    使用方式::

        rp = RepairPersistence()
        rp.record("title", ".title", ".product-name", "https://demo.local/item/1", True)
        suggestion = rp.suggest("title", "https://demo.local/item/2")
    """

    def __init__(self, db_path: str = "~/.generic_crawler/repairs.jsonl"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    # ── 公开方法 ──────────────────────────────────────────────────

    def record(
        self,
        field_name: str,
        old_selector: str,
        new_selector: str,
        page_url: str,
        success: bool,
    ) -> None:
        """记录一次修复尝试。

        Args:
            field_name: 字段名（如 "商品标题"）。
            old_selector: 失效的原始选择器。
            new_selector: 修复后的新选择器。
            page_url: 触发修复的页面 URL。
            success: 修复后提取是否成功（通过质量校验）。
        """
        entry: Dict[str, Any] = {
            "at": _utc_now(),
            "page_pattern": self._url_pattern(page_url),
            "page_url": page_url,
            "field": field_name,
            "old": old_selector,
            "new": new_selector,
            "ok": success,
        }
        with open(self.db_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def suggest(self, field_name: str, page_url: str = "") -> Optional[str]:
        """查找历史上对此字段最成功的修复选择器。

        匹配优先级：
        1. 相同页面模式 + 成功 → 得分 3
        2. 相同字段名 + 成功   → 得分 2
        3. 相同字段名 + 失败   → 得分 0（跳过）

        Args:
            field_name: 字段名。
            page_url: 当前页面 URL（用于匹配页面模式）。

        Returns:
            高置信度替代选择器，无匹配时返回 None。
        """
        if not self.db_path.exists():
            return None

        page_pattern = self._url_pattern(page_url) if page_url else ""
        candidates: List[Dict[str, Any]] = []

        with open(self.db_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("field") != field_name:
                    continue
                if not entry.get("ok"):
                    continue

                score = 2  # 同字段名的基础分
                if page_pattern and entry.get("page_pattern") == page_pattern:
                    score = 3  # 同页面模式加分
                entry["_score"] = score
                candidates.append(entry)

        if not candidates:
            return None

        # 按得分降序、时间降序排列
        candidates.sort(key=lambda e: (e["_score"], e.get("at", "")), reverse=True)
        return candidates[0].get("new")

    def stats(self) -> Dict[str, Any]:
        """返回修复统计摘要。

        Returns:
            {'total': N, 'success': N, 'rate': 0.0~1.0}
        """
        if not self.db_path.exists():
            return {"total": 0, "success": 0, "rate": 0.0}

        total = 0
        success = 0
        with open(self.db_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    total += 1
                    if entry.get("ok"):
                        success += 1
                except json.JSONDecodeError:
                    continue

        return {
            "total": total,
            "success": success,
            "rate": success / max(total, 1),
        }

    # ── 内部方法 ──────────────────────────────────────────────────

    @staticmethod
    def _url_pattern(url: str) -> str:
        """将具体 URL 抽象为页面模式。

        ``https://item.taobao.com/item.htm?id=123456``
        → ``*item.taobao.com/item.htm``

        规则：路径中纯数字段和长哈希段替换为 ``*``。
        """
        try:
            parsed = urlparse(url)
        except Exception:
            return "*unknown"

        host = parsed.netloc or "unknown"
        segments = [seg for seg in parsed.path.strip("/").split("/") if seg]

        pattern_parts: List[str] = []
        for seg in segments:
            if seg.isdigit() or len(seg) > 32:
                pattern_parts.append("*")
            else:
                pattern_parts.append(seg)

        return f"*{host}/" + "/".join(pattern_parts) if pattern_parts else f"*{host}"
