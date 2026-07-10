from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from core.spider_engine import GenericSpider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="配置驱动爬虫入口")
    parser.add_argument(
        "-c",
        "--config",
        default="configs/taobao.yaml",
        help="爬虫配置文件（YAML）路径",
    )
    parser.add_argument(
        "--start-url",
        help="覆盖 start_urls 的单条起始 URL（支持淘宝商品页）",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="输出文件路径（可选，支持 .json / .jsonl / .csv）",
    )
    parser.add_argument(
        "--format",
        choices=["json", "jsonl", "csv"],
        default="json",
        help="无输出文件时控制终端输出格式",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="日志级别（默认 INFO）",
    )
    return parser.parse_args()


def load_config(config_path: str) -> Dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"配置文件内容必须是字典: {config_path}")
    return data


def dump_json(records: Iterable[Dict[str, Any]]) -> str:
    return json.dumps(list(records), ensure_ascii=False, indent=2)


def write_csv(records: Iterable[Dict[str, Any]], path: Path) -> None:
    rows = list(records)
    headers = []
    for row in rows:
        for key in row.keys():
            if key not in headers:
                headers.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def enrich_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for row in records:
        row.setdefault("爬取时间", now)
        row.setdefault("crawl_time", now)
    return records


def output_records(records: List[Dict[str, Any]], output: Optional[str], fmt: str) -> None:
    records = enrich_records(records)
    if not output:
        if fmt == "jsonl":
            print("\n".join(json.dumps(item, ensure_ascii=False) for item in records))
        elif fmt == "csv":
            if not records:
                print("")
                return
            headers = []
            for row in records:
                for key in row.keys():
                    if key not in headers:
                        headers.append(key)
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=headers)
            writer.writeheader()
            writer.writerows(records)
            print(buffer.getvalue().strip())
        else:
            print(dump_json(records))
        return

    out_file = Path(output)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    ext = out_file.suffix.lower()
    if ext == ".jsonl":
        text = "\n".join(json.dumps(item, ensure_ascii=False) for item in records)
        out_file.write_text(text, encoding="utf-8")
    elif ext == ".csv":
        write_csv(records, out_file)
    else:
        out_file.write_text(dump_json(records), encoding="utf-8")


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    config = load_config(args.config)

    if args.start_url:
        config["start_urls"] = [args.start_url]
        config["start_url"] = args.start_url

    spider = GenericSpider(config)
    records = await spider.run()
    output_records(records, args.output, args.format)


if __name__ == "__main__":
    asyncio.run(main())
