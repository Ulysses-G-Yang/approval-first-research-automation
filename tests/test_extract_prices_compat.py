from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import extract_prices
from core.spider_engine import GenericSpider


class _FakeSpider:
    def __init__(self, config):
        self.config = config

    async def run(self):
        return [{"title": "Fixture", "url": self.config["start_url"]}]


class ExtractPricesCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def test_historical_generic_spider_import_remains_available(self) -> None:
        self.assertIs(extract_prices.GenericSpider, GenericSpider)

    async def test_legacy_entry_keeps_config_override_and_output_formats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "crawler.yaml"
            config.write_text(
                "name: fixture\nstart_url: https://fixture.invalid/original\n",
                encoding="utf-8",
            )

            for extension in ("json", "jsonl", "csv"):
                destination = root / f"records.{extension}"
                await extract_prices.main(
                    [
                        "--config",
                        str(config),
                        "--start-url",
                        "https://fixture.invalid/override",
                        "--output",
                        str(destination),
                        "--format",
                        extension,
                    ],
                    spider_factory=_FakeSpider,
                )
                self.assertTrue(destination.is_file())

            json_rows = json.loads((root / "records.json").read_text(encoding="utf-8"))
            jsonl_rows = [
                json.loads(line)
                for line in (root / "records.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            with (root / "records.csv").open("r", encoding="utf-8-sig", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))

            for rows in (json_rows, jsonl_rows, csv_rows):
                self.assertEqual(rows[0]["title"], "Fixture")
                self.assertEqual(rows[0]["url"], "https://fixture.invalid/override")
