"""Backward-compatible source-checkout wrapper for the installed crawler CLI.

``python extract_prices.py --config ...`` keeps the historical command shape.
New installations should prefer ``crawler run --config ...``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable

from core.crawler_cli import (
    dump_json,
    enrich_records,
    legacy_main,
    load_config,
    output_records,
    parse_legacy_args,
    write_csv,
)
from core.spider_engine import GenericSpider


def parse_args(argv: Iterable[str] | None = None):
    """Preserve the original no-argument parser helper."""

    return parse_legacy_args(argv)


async def main(
    argv: Iterable[str] | None = None,
    *,
    spider_factory: Any = None,
) -> None:
    await legacy_main(argv, spider_factory=spider_factory)


if __name__ == "__main__":
    asyncio.run(main())


__all__ = [
    "dump_json",
    "enrich_records",
    "GenericSpider",
    "load_config",
    "main",
    "output_records",
    "parse_args",
    "write_csv",
]
