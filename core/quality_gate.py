"""
字段级质量校验模块。

用于验证采集提取结果是否符合预期，支持多种内置校验规则。
所有规则可自由组合，校验结果包含通过/失败状态、评分和失败原因列表。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ValidationResult:
    """单次校验结果"""

    passed: bool
    """所有规则是否全部通过"""
    score: float
    """校验评分 0.0 ~ 1.0（通过规则数 / 总规则数）"""
    failed_rules: List[str] = field(default_factory=list)
    """未通过的规则名称列表"""


class QualityGate:
    """字段级质量校验器。

    支持的校验规则：

    - non_empty: {}                  — 值非空
    - min_length: 2                 — 最小字符长度
    - max_length: 200               — 最大字符长度
    - type: "price"                 — 价格（正浮点数）
    - type: "int"                   — 整数
    - type: "float"                 — 浮点数
    - type: "url"                   — HTTP(S) URL
    - type: "date"                  — 日期（YYYY-MM-DD / YYYY/MM/DD）
    - type: "email"                 — 邮箱地址
    - regex: {"pattern": "^\\d+"}   — 正则匹配
    - enum: {"values": ["a","b"]}   — 枚举白名单
    - length_range: {"min": 1, "max": 100} — 长度范围
    """

    @staticmethod
    def validate(value: Any, validation: Optional[Dict[str, Any]] = None) -> ValidationResult:
        """对给定值执行校验规则。

        Args:
            value: 待校验的值。
            validation: 规则字典，如 ``{"non_empty": {}, "type": "price"}``。
                       为 None 或不传时默认只检查非空。

        Returns:
            ValidationResult: 校验结果。
        """
        text = str(value).strip() if value is not None else ""

        if not validation:
            # 默认规则：只看非空
            passed = bool(text)
            return ValidationResult(
                passed=passed,
                score=1.0 if passed else 0.0,
                failed_rules=[] if passed else ["non_empty"],
            )

        total = len(validation)
        failed: List[str] = []

        for rule_name, params in validation.items():
            handler = _VALIDATORS.get(rule_name)
            if handler is None:
                # 未知规则——记录但不算失败，避免因配置错误阻断采集
                continue
            try:
                if not handler(text, params):
                    failed.append(_format_failure(rule_name, params))
            except Exception:
                failed.append(f"{rule_name}:eval_error")

        passed = len(failed) == 0
        score = (total - len(failed)) / total if total > 0 else 1.0
        return ValidationResult(passed=passed, score=score, failed_rules=failed)


# ═══════════════════════════════════════════════════════════════════
# 内置校验器
# ═══════════════════════════════════════════════════════════════════

def _validate_non_empty(text: str, _params: Any) -> bool:
    return bool(text)


def _validate_min_length(text: str, min_len: int) -> bool:
    return len(text) >= int(min_len)


def _validate_max_length(text: str, max_len: int) -> bool:
    return len(text) <= int(max_len)


def _validate_length_range(text: str, params: Dict) -> bool:
    lo = int(params.get("min", 0) or 0)
    hi = int(params.get("max", 999999) or 999999)
    return lo <= len(text) <= hi


def _validate_type_price(text: str) -> bool:
    cleaned = re.sub(r"[^\d.\-]", "", text)
    if not cleaned:
        return False
    try:
        return float(cleaned) > 0
    except ValueError:
        return False


def _validate_type_int(text: str) -> bool:
    cleaned = text.replace(",", "").replace(" ", "")
    try:
        int(cleaned)
        return True
    except ValueError:
        return False


def _validate_type_float(text: str) -> bool:
    cleaned = text.replace(",", "").replace(" ", "")
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def _validate_type_url(text: str) -> bool:
    return bool(re.match(r"^https?://[^\s]+", text))


def _validate_type_date(text: str) -> bool:
    return bool(re.match(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", text))


def _validate_type_email(text: str) -> bool:
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", text))


def _validate_regex(text: str, params: Dict) -> bool:
    pattern = params.get("pattern", "")
    if not pattern:
        return True
    return bool(re.match(pattern, text))


def _validate_enum(text: str, params: Dict) -> bool:
    allowed = params.get("values", [])
    if not isinstance(allowed, (list, tuple)):
        allowed = []
    return text in [str(v) for v in allowed]


# ── 类型分发 ──────────────────────────────────────────────────────

def _validate_type(text: str, type_name: str) -> bool:
    handlers = {
        "price": _validate_type_price,
        "int": _validate_type_int,
        "float": _validate_type_float,
        "url": _validate_type_url,
        "date": _validate_type_date,
        "email": _validate_type_email,
    }
    handler = handlers.get(type_name)
    if handler is None:
        return True  # 未知类型不阻断
    return handler(text)


# ── 校验器注册表 ───────────────────────────────────────────────────

_VALIDATORS = {
    "non_empty": lambda t, p: _validate_non_empty(t, p),
    "min_length": lambda t, p: _validate_min_length(t, p),
    "max_length": lambda t, p: _validate_max_length(t, p),
    "length_range": lambda t, p: _validate_length_range(t, p),
    "type": lambda t, p: _validate_type(t, p),
    "regex": lambda t, p: _validate_regex(t, p),
    "enum": lambda t, p: _validate_enum(t, p),
}


def _format_failure(rule_name: str, params: Any) -> str:
    if rule_name == "type" and isinstance(params, str):
        return f"type:{params}"
    if rule_name == "regex" and isinstance(params, dict):
        return f"regex:{params.get('pattern', '?')}"
    if rule_name == "enum" and isinstance(params, dict):
        return f"enum:{params.get('values', [])}"
    if rule_name == "length_range" and isinstance(params, dict):
        return f"length_range:[{params.get('min',0)},{params.get('max',0)}]"
    return f"{rule_name}:{params}"
