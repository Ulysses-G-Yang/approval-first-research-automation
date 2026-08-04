from __future__ import annotations

import tempfile
import unittest
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from adapters.base import ExtractionField, PlatformAdapter
from core.extraction_pipeline import FieldExtractionPipeline
from core.experience_store import ExperienceStore
from core.repair_persistence import RepairPersistence
from core.self_healing import SelfHealingEngine
from core.spider_engine import GenericSpider


class FakeElement:
    def __init__(
        self,
        text: str,
        attributes: dict[str, str] | None = None,
    ):
        self.text = text
        self.attributes = attributes or {}

    async def inner_text(self) -> str:
        return self.text

    async def get_attribute(self, name: str) -> str:
        return self.attributes.get(name, "")


class FakePage:
    url = "https://example.test/catalog/1"

    def __init__(self, values: dict[str, str | FakeElement]):
        self.values = values

    async def query_selector(self, selector: str):
        value = self.values.get(selector)
        if isinstance(value, FakeElement):
            return value
        return FakeElement(value) if value is not None else None

    async def content(self) -> str:
        return "<main></main>"


class FieldExtractionPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_generic_spider_runs_fallback_without_a_configured_selector(self) -> None:
        spider = GenericSpider(
            {
                "start_url": FakePage.url,
                "enable_adaptive": False,
                "fields": [
                    {
                        "name": "title",
                        "fallback_selectors": [".fallback"],
                        "validation": {"non_empty": {}},
                    }
                ],
            }
        )
        page = FakePage({".fallback": "fallback value"})

        records = await spider._extract_fields(page, {"page_url": page.url})

        self.assertEqual(records, [{"title": "fallback value"}])

    async def test_approved_history_runs_before_adaptive_and_llm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = RepairPersistence(str(Path(temp_dir) / "repairs.jsonl"))
            memory.record("title", ".old", ".unapproved", FakePage.url, True)
            memory.record(
                "title",
                ".old",
                ".approved",
                FakePage.url,
                True,
                approved=True,
            )
            pipeline = FieldExtractionPipeline(repair_memory=memory)
            calls: list[str] = []

            async def extract(selector: str, _attr: str | None) -> str:
                calls.append(selector)
                return {
                    ".old": "x",
                    ".fallback": "y",
                    ".approved": "approved value",
                    ".unapproved": "must not run",
                }.get(selector, "")

            adaptive = AsyncMock(return_value="adaptive value")
            llm = AsyncMock(return_value=".llm")
            result = await pipeline.extract(
                {
                    "name": "title",
                    "selector": ".old",
                    "fallback_selectors": [".fallback"],
                    "validation": {"min_length": 3},
                },
                page_url=FakePage.url,
                selector_extractor=extract,
                adaptive_extractor=adaptive,
                llm_candidate=llm,
            )

            self.assertEqual(result.method, "cached_repair")
            self.assertEqual(result.selector, ".approved")
            self.assertEqual(calls, [".old", ".fallback", ".approved"])
            adaptive.assert_not_awaited()
            llm.assert_not_awaited()

    async def test_llm_candidate_is_reextracted_and_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "repairs.jsonl"
            memory = RepairPersistence(str(db_path))
            pipeline = FieldExtractionPipeline(repair_memory=memory)
            calls: list[str] = []

            async def extract(selector: str, _attr: str | None) -> str:
                calls.append(selector)
                return "valid value" if selector == ".llm" else ""

            result = await pipeline.extract(
                {
                    "name": "title",
                    "selector": ".old",
                    "validation": {"min_length": 5},
                },
                page_url=FakePage.url,
                selector_extractor=extract,
                llm_candidate=AsyncMock(return_value=".llm"),
            )

            self.assertEqual(result.method, "llm_text")
            self.assertEqual(calls, [".old", ".llm"])
            self.assertFalse(db_path.exists())

    async def test_adaptive_and_llm_candidates_use_the_same_quality_gate(self) -> None:
        pipeline = FieldExtractionPipeline()

        async def extract(selector: str, _attr: str | None) -> str:
            return "valid value" if selector == ".llm" else ""

        result = await pipeline.extract(
            {
                "name": "title",
                "selector": ".old",
                "validation": {"min_length": 5},
            },
            page_url=FakePage.url,
            selector_extractor=extract,
            adaptive_extractor=AsyncMock(return_value="x"),
            llm_candidate=AsyncMock(return_value=".llm"),
        )

        self.assertEqual(result.method, "llm_text")
        self.assertEqual(result.value, "valid value")

    async def test_low_confidence_adaptive_result_invokes_llm_and_survives_rejection(self) -> None:
        pipeline = FieldExtractionPipeline()
        llm = AsyncMock(return_value=".bad")

        async def extract(selector: str, _attr: str | None) -> str:
            return "no" if selector == ".bad" else ""

        result = await pipeline.extract(
            {
                "name": "title",
                "selector": ".old",
                "validation": {"min_length": 5},
            },
            page_url=FakePage.url,
            selector_extractor=extract,
            adaptive_extractor=AsyncMock(return_value="adaptive value"),
            llm_candidate=llm,
        )

        llm.assert_awaited_once()
        self.assertEqual(result.method, "scrapling_adaptive")
        self.assertEqual(result.value, "adaptive value")
        self.assertEqual(result.attempts[-1]["stage"], "llm_text")
        self.assertFalse(result.attempts[-1]["accepted"])

    async def test_llm_candidate_attempt_is_written_as_pending_episode_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with ExperienceStore(Path(temp_dir) / "episodes.sqlite3") as store:
                spider = GenericSpider(
                    {
                        "name": "fixture",
                        "authorization_category": "synthetic_local",
                        "enable_adaptive": False,
                        "llm": {
                            "enable_repair": False,
                            "provider": "fixture",
                            "model": "fixture-model",
                            "prompt_version": "selector-repair-test-v1",
                        },
                    },
                    experience_store=store,
                )
                spider.enable_llm_repair = True
                spider.llm_repair = SimpleNamespace(
                    repair_selector=AsyncMock(return_value=".llm-bad"),
                    provider="runtime-fixture",
                    model="runtime-model",
                )
                page = FakePage({".llm-bad": "wrong"})

                value = await spider._extract_field_adaptive(
                    page,
                    {
                        "name": "title",
                        "selector": ".missing",
                        "validation": {"enum": {"values": ["expected"]}},
                    },
                )

                self.assertEqual(value, "")
                payload = store.get_episode(spider.repair_episode_ids[0])
                self.assertEqual(len(payload["proposals"]), 1)
                self.assertEqual(
                    payload["proposals"][0]["patch"]["fields"][0]["selector"],
                    ".llm-bad",
                )
                self.assertEqual(payload["proposals"][0]["status"], "candidate")
                self.assertFalse(payload["validations"][0]["passed"])
                self.assertEqual(payload["decisions"], [])
                self.assertEqual(
                    payload["episode"]["metadata"]["prompt_version"],
                    "selector-repair-test-v1",
                )
                self.assertEqual(payload["episode"]["metadata"]["provider"], "runtime-fixture")
                self.assertEqual(payload["episode"]["metadata"]["model"], "runtime-model")

    async def test_self_healing_facade_uses_the_shared_fallback_path(self) -> None:
        engine = SelfHealingEngine(enable_scrapling=False)
        result = await engine.extract_with_healing(
            FakePage({".old": "x", ".fallback": "valid value"}),
            {
                "name": "title",
                "selector": ".old",
                "fallback_selectors": [".fallback"],
                "validation": {"min_length": 5},
            },
        )

        self.assertIsInstance(engine.extraction_pipeline, FieldExtractionPipeline)
        self.assertIs(engine.extraction_pipeline, engine._spider.extraction_pipeline)
        self.assertIs(engine.quality_gate, engine._spider.quality_gate)
        self.assertIs(engine.repair_memory, engine._spider.repair_memory)
        self.assertEqual(result.method, "fallback")
        self.assertEqual(result.value, "valid value")

    async def test_self_healing_facade_constructs_existing_llm_repair(self) -> None:
        repairer = SimpleNamespace(repair_selector=AsyncMock(return_value=""))
        with patch("core.spider_engine.LLMRepair", return_value=repairer) as factory:
            engine = SelfHealingEngine(
                enable_llm=True,
                llm_model="qwen3",
                enable_scrapling=False,
            )

        llm_config = factory.call_args.args[0]
        self.assertEqual(llm_config["provider"], "ollama")
        self.assertEqual(llm_config["model"], "qwen3")
        self.assertNotIn("secret_ref", llm_config)
        self.assertEqual(llm_config["timeout"], 15)
        self.assertIs(engine.llm_repair, repairer)
        self.assertIs(engine._spider.llm_repair, repairer)

    async def test_self_healing_facade_lazily_enables_local_llm_and_rejects_string_flags(self) -> None:
        with self.assertRaisesRegex(TypeError, "enable_llm"):
            SelfHealingEngine(enable_llm="false")  # type: ignore[arg-type]

        engine = SelfHealingEngine(enable_llm=False, enable_scrapling=False)
        engine.enable_llm = True
        repairer = SimpleNamespace(
            provider="ollama",
            model="qwen3",
            repair_selector=AsyncMock(return_value=".repaired"),
        )
        with patch("core.self_healing.LLMRepair", return_value=repairer) as factory:
            result = await engine.extract_with_healing(
                FakePage({".repaired": "repaired value"}),
                {"name": "title", "selector": ".old"},
            )

        factory.assert_called_once()
        self.assertEqual(result.value, "repaired value")
        self.assertIs(engine._spider.llm_repair, repairer)

    async def test_self_healing_facade_preserves_attr_for_fallback(self) -> None:
        engine = SelfHealingEngine(enable_scrapling=False)
        expected = "https://fixture.invalid/images/notebook.png"
        result = await engine.extract_with_healing(
            FakePage(
                {
                    ".old": FakeElement("wrong text"),
                    ".fallback": FakeElement(
                        "wrong text",
                        {"src": expected},
                    ),
                }
            ),
            {
                "name": "image",
                "selector": ".old",
                "fallback_selectors": [".fallback"],
                "attr": "src",
                "validation": {"type": "url"},
            },
        )

        self.assertEqual(result.method, "fallback")
        self.assertEqual(result.value, expected)

    async def test_self_healing_facade_passes_attr_to_generic_adaptive(self) -> None:
        engine = SelfHealingEngine(enable_scrapling=True)
        expected = "https://fixture.invalid/images/adaptive.png"
        adaptive = AsyncMock(return_value=expected)
        engine._spider._extract_from_scrapling = adaptive

        result = await engine.extract_with_healing(
            FakePage({}),
            {
                "name": "image",
                "selector": ".old",
                "attr": "src",
                "validation": {"type": "url"},
            },
        )

        self.assertEqual(result.method, "scrapling_adaptive")
        self.assertEqual(result.value, expected)
        self.assertEqual(adaptive.await_args.kwargs["attr"], "src")

    async def test_self_healing_facade_reextracts_llm_candidate_with_attr(self) -> None:
        engine = SelfHealingEngine(enable_scrapling=False)
        engine.enable_llm = True
        candidate = AsyncMock(return_value=".repaired")
        engine.llm_repair = SimpleNamespace(repair_selector=candidate)
        expected = "https://fixture.invalid/images/repaired.png"

        result = await engine.extract_with_healing(
            FakePage(
                {
                    ".repaired": FakeElement(
                        "wrong text",
                        {"src": expected},
                    )
                }
            ),
            {
                "name": "image",
                "selector": ".old",
                "attr": "src",
                "validation": {"type": "url"},
            },
        )

        candidate.assert_awaited_once()
        self.assertEqual(result.method, "llm_text")
        self.assertEqual(result.value, expected)

    async def test_generic_spider_uses_the_shared_fallback_and_validation_path(self) -> None:
        spider = GenericSpider({"enable_adaptive": False})
        value = await spider._extract_field_adaptive(
            FakePage({".old": "x", ".fallback": "valid value"}),
            {
                "name": "title",
                "selector": ".old",
                "fallback_selectors": [".fallback"],
                "validation": {"min_length": 5},
            },
        )

        self.assertIsInstance(spider.extraction_pipeline, FieldExtractionPipeline)
        self.assertEqual(value, "valid value")


class RepairPersistenceTests(unittest.TestCase):
    def test_default_store_is_disabled(self) -> None:
        memory = RepairPersistence()
        self.assertFalse(memory.enabled)
        self.assertIsNone(memory.db_path)

    def test_disabled_store_does_not_create_directories_or_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "disabled" / "repairs.jsonl"
            memory = RepairPersistence(str(db_path), enabled=False)
            memory.record("title", ".old", ".new", FakePage.url, True, approved=True)

            self.assertFalse(db_path.parent.exists())
            self.assertIsNone(memory.suggest("title", FakePage.url))
            self.assertEqual(memory.stats(), {"total": 0, "success": 0, "rate": 0.0})

    def test_successful_but_unapproved_history_is_not_suggested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = RepairPersistence(str(Path(temp_dir) / "repairs.jsonl"))
            memory.record("title", ".old", ".new", FakePage.url, True)

            self.assertIsNone(memory.suggest("title", FakePage.url))

    def test_repair_history_redacts_urls_and_requires_literal_results(self) -> None:
        marker = "repair-url-secret-f61a"
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "repairs.jsonl"
            memory = RepairPersistence(str(db_path))
            memory.record(
                "title",
                ".old",
                ".approved",
                f"https://user:{marker}@example.test/items/1?authToken={marker}",
                True,
                approved=True,
            )
            self.assertNotIn(marker, db_path.read_text(encoding="utf-8"))
            self.assertEqual(memory.suggest("title", "https://example.test/items/2"), ".approved")

            with self.assertRaisesRegex(TypeError, "true or false"):
                memory.record(
                    "title",
                    ".old",
                    ".bad",
                    FakePage.url,
                    "false",  # type: ignore[arg-type]
                    approved=True,
                )

            with db_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "at": "2099-01-01T00:00:00+00:00",
                            "page_pattern": "*example.test/items/*",
                            "page_url": "https://example.test/items/3",
                            "field": "other",
                            "old": ".old",
                            "new": ".must-not-run",
                            "ok": "false",
                            "approved": True,
                        }
                    )
                    + "\n"
                )
            self.assertIsNone(memory.suggest("other", "https://example.test/items/3"))


class ExampleAdapter(PlatformAdapter):
    platform_name = "example"

    def match(self, url: str) -> bool:
        return "example.test" in url

    def get_item_selector(self) -> str:
        return ".item"

    def get_fields(self) -> list[ExtractionField]:
        return [
            ExtractionField(
                name="title",
                description="catalog title",
                selector=".title",
                fallback_selectors=[".headline"],
                validation={"min_length": 3},
            )
        ]

    def post_process(self, records: list[dict[str, object]]) -> list[dict[str, object]]:
        return [{**record, "processed": True} for record in records]


class AdapterIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def test_generic_spider_keeps_store_injection_keyword_only(self) -> None:
        parameters = inspect.signature(GenericSpider).parameters
        self.assertEqual(
            parameters["experience_store"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )

    async def test_from_adapter_preserves_field_policy_and_applies_post_process(self) -> None:
        adapter = ExampleAdapter()
        config = adapter.to_config(FakePage.url)
        self.assertEqual(config["fields"][0]["fallback_selectors"], [".headline"])
        self.assertEqual(config["fields"][0]["validation"], {"min_length": 3})
        self.assertIsNone(GenericSpider(config)._adapter_post_process)

        class FakePageForRun:
            async def close(self) -> None:
                return None

        class FakeContext:
            async def new_page(self):
                return FakePageForRun()

            async def close(self) -> None:
                return None

        class FakeBrowser:
            contexts: list[object] = []

            async def new_context(self, **_kwargs):
                return FakeContext()

            async def close(self) -> None:
                return None

        class FakePlaywrightManager:
            async def __aenter__(self):
                return SimpleNamespace(
                    chromium=SimpleNamespace(launch=AsyncMock(return_value=FakeBrowser()))
                )

            async def __aexit__(self, *_args):
                return None

        spider = GenericSpider.from_adapter(adapter, FakePage.url)
        with patch(
            "core.spider_engine.async_playwright",
            return_value=FakePlaywrightManager(),
        ), patch.object(
            spider,
            "_crawl_pages",
            new=AsyncMock(return_value=[{"title": "Example"}]),
        ):
            records = await spider.run()

        self.assertEqual(records, [{"title": "Example", "processed": True}])


if __name__ == "__main__":
    unittest.main()
