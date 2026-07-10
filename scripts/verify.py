#!/usr/bin/env python3
"""Run an opt-in live verification of the selector and adaptive extraction paths."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import yaml


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FIELDS = ("title", "rating")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证通用爬虫的常规与自适应提取路径")
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        help="保留输出和破损配置的目录；未指定时使用临时目录并在结束后清理。",
    )
    return parser.parse_args()


def run_crawler(config_path: Path, output_path: Path) -> Tuple[int, str, str]:
    command = [
        sys.executable,
        str(ROOT / "extract_prices.py"),
        "--config",
        str(config_path),
        "--output",
        str(output_path),
        "--log-level",
        "INFO",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )
    return result.returncode, result.stdout, result.stderr


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError("输出文件不存在")

    records: List[Dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_number} 行不是合法 JSONL: {exc}") from exc
        if isinstance(row, dict):
            records.append(row)
    return records


def validate_records(
    records: Iterable[Dict[str, Any]],
    minimum: int,
    required_fields: Sequence[str] = REQUIRED_FIELDS,
) -> Tuple[bool, str]:
    rows = list(records)
    valid_rows = [
        row
        for row in rows
        if all(str(row.get(field, "")).strip() for field in required_fields)
    ]
    if len(valid_rows) < minimum:
        return (
            False,
            f"仅 {len(valid_rows)}/{len(rows)} 条记录包含非空字段 {', '.join(required_fields)}，"
            f"少于预期 {minimum} 条",
        )
    return True, f"{len(valid_rows)}/{len(rows)} 条记录包含非空字段 {', '.join(required_fields)}"


def make_broken_config(destination: Path) -> None:
    source = ROOT / "configs" / "douban.yaml"
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("configs/douban.yaml 必须是 YAML 对象")

    config["name"] = "douban-top250-adaptive-verification"
    config["enable_adaptive"] = True
    config.setdefault("pagination", {})["max_pages"] = 1
    config.setdefault("llm", {})["enable_repair"] = False

    for field in config.get("fields", []):
        if isinstance(field, dict) and field.get("name") in REQUIRED_FIELDS:
            field["selector"] = f".broken-{field['name']}-selector"

    destination.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def assert_success(name: str, rc: int, stdout: str, stderr: str, output_path: Path, minimum: int) -> bool:
    if rc != 0:
        print(f"[FAIL] {name} 执行失败\n{stderr or stdout}")
        return False
    try:
        records = read_jsonl(output_path)
        ok, message = validate_records(records, minimum)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] {name}: {exc}")
        return False

    if not ok:
        print(f"[FAIL] {name}: {message}")
        return False
    print(f"[OK] {name}: {message}")
    return True


def verify(work_dir: Path) -> int:
    normal_output = work_dir / "douban-normal.jsonl"
    broken_config = work_dir / "douban-broken.yaml"
    broken_output = work_dir / "douban-broken.jsonl"

    print("=== 验证通用爬虫提取链路 ===")
    print("1. 运行正常豆瓣配置")
    rc, stdout, stderr = run_crawler(ROOT / "configs" / "douban.yaml", normal_output)
    if not assert_success("正常 selector", rc, stdout, stderr, normal_output, minimum=5):
        return 1

    print("2. 注入破损 selector 并验证 Scrapling 自适应路径")
    make_broken_config(broken_config)
    rc, stdout, stderr = run_crawler(broken_config, broken_output)
    if not assert_success("故障注入 selector", rc, stdout, stderr, broken_output, minimum=1):
        return 1
    if "ADAPTIVE_SUCCESS" not in stderr:
        print("[FAIL] 故障注入没有观察到 Scrapling 自适应成功事件")
        return 1
    print("[OK] 故障注入已通过 Scrapling 自适应路径恢复关键字段")

    print("3. LLM 修复保持默认关闭；离线 mock 测试覆盖其缓存和降级逻辑")
    print(f"验证产物目录: {work_dir}")
    return 0


def main() -> int:
    args = parse_args()
    if args.artifacts_dir:
        args.artifacts_dir.mkdir(parents=True, exist_ok=True)
        return verify(args.artifacts_dir.resolve())

    with tempfile.TemporaryDirectory(prefix="generic-crawler-verify-") as directory:
        return verify(Path(directory))


if __name__ == "__main__":
    raise SystemExit(main())
