from __future__ import annotations

import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

from core.captures import PageCaptureSession
from core.extraction_pipeline import FieldExtractionPipeline
from core.experience_store import ExperienceStore
from core.js_benchmark import (
    SCHEMA_VERSION,
    baseline_failures,
    check_baseline,
    deterministic_projection,
    run_benchmark,
    run_benchmark_async,
)
from core.spider_engine import GenericSpider


class JsEvolutionBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_benchmark()

    def test_v21_release_baseline_is_met(self) -> None:
        self.assertEqual(self.report["schema_version"], SCHEMA_VERSION)
        self.assertEqual(self.report["corpus_id"], "js-evolution-v1")
        self.assertTrue(self.report["passed"], self.report["baseline_failures"])
        self.assertTrue(check_baseline(self.report))
        self.assertEqual(baseline_failures(self.report), [])

        metrics = self.report["metrics"]
        self.assertEqual(metrics["normal_exact_match"]["rate"], 1.0)
        self.assertEqual(metrics["recoverable_recovery"]["rate"], 1.0)
        self.assertEqual(metrics["invalid_candidate_acceptance"]["rate"], 0.0)
        self.assertEqual(metrics["irrecoverable_correct_failure"]["rate"], 1.0)
        self.assertEqual(metrics["expected_stage_match"]["rate"], 1.0)
        self.assertEqual(metrics["external_network_calls"], 0)
        self.assertEqual(metrics["real_model_calls"], 0)
        self.assertTrue(metrics["artifact_hashes_deterministic"])
        self.assertTrue(metrics["non_timing_metrics_deterministic"])
        self.assertIn("elapsed_ms", metrics)

    def test_corpus_exercises_every_required_evolution_family(self) -> None:
        cases = {case["case_id"]: case for case in self.report["cases"]}
        self.assertEqual(
            set(cases),
            {
                "stable-dom-selector",
                "fallback-after-class-drift",
                "embedded-json-location-and-path-drift",
                "network-json-endpoint-and-schema-drift",
                "delayed-hydration-wait-drift",
                "invalid-nonempty-candidate",
                "irrecoverable-missing-data",
            },
        )
        self.assertEqual(cases["stable-dom-selector"]["pipeline_stage"], "configured")
        self.assertEqual(cases["fallback-after-class-drift"]["pipeline_stage"], "fallback")
        self.assertEqual(
            cases["embedded-json-location-and-path-drift"]["pipeline_stage"],
            "embedded_json_repair",
        )
        self.assertEqual(
            cases["network-json-endpoint-and-schema-drift"]["pipeline_stage"],
            "network_json_repair",
        )
        self.assertEqual(
            cases["delayed-hydration-wait-drift"]["pipeline_stage"],
            "wait_condition_repair",
        )
        invalid = cases["invalid-nonempty-candidate"]
        self.assertEqual(invalid["pipeline_stage"], "quality_gate_rejected")
        self.assertFalse(invalid["accepted_invalid_candidate"])
        self.assertEqual(invalid["actual_value"], "")
        self.assertEqual(cases["irrecoverable-missing-data"]["pipeline_stage"], "exhausted")
        self.assertEqual(cases["irrecoverable-missing-data"]["actual_value"], "")

    def test_case_output_has_episode_and_deterministic_artifact_identity(self) -> None:
        for case in self.report["cases"]:
            self.assertTrue(case["repair_episode_id"])
            self.assertEqual(len(case["artifact_hash"]), 64)
            self.assertEqual(len(case["fixture_hash"]), 64)
            self.assertEqual(case["expected_stage"], case["pipeline_stage"])

        repeated = run_benchmark()
        self.assertEqual(
            deterministic_projection(self.report),
            deterministic_projection(repeated),
        )

    def test_baseline_checker_rejects_a_regression(self) -> None:
        regressed = copy.deepcopy(self.report)
        regressed["metrics"]["invalid_candidate_acceptance"]["accepted"] = 1
        regressed["metrics"]["invalid_candidate_acceptance"]["rate"] = 1.0
        self.assertFalse(check_baseline(regressed))
        self.assertTrue(baseline_failures(regressed))


class JsEvolutionAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_entry_point_supports_existing_event_loop(self) -> None:
        report = await run_benchmark_async()
        self.assertTrue(report["passed"])
        with self.assertRaisesRegex(RuntimeError, "active event loop"):
            run_benchmark()

    async def test_capture_cases_fail_when_production_capture_is_broken(self) -> None:
        broken_finish = AsyncMock(return_value={})
        with patch.object(PageCaptureSession, "finish", new=broken_finish):
            report = await run_benchmark_async()

        self.assertFalse(report["passed"])
        failed = {case["case_id"] for case in report["cases"] if not case["passed"]}
        self.assertIn("embedded-json-location-and-path-drift", failed)
        self.assertIn("network-json-endpoint-and-schema-drift", failed)
        self.assertGreater(broken_finish.await_count, 0)

    async def test_hydration_case_fails_when_production_prepare_is_broken(self) -> None:
        broken_prepare = AsyncMock(return_value=None)
        with patch.object(GenericSpider, "_prepare_page", new=broken_prepare):
            report = await run_benchmark_async()

        self.assertFalse(report["passed"])
        hydration = next(
            case
            for case in report["cases"]
            if case["case_id"] == "delayed-hydration-wait-drift"
        )
        self.assertFalse(hydration["passed"])
        self.assertGreater(broken_prepare.await_count, 0)

    async def test_invalid_candidate_requires_an_actual_pipeline_attempt(self) -> None:
        original_extract = FieldExtractionPipeline.extract

        async def skip_invalid_candidate(self, field, **kwargs):
            validation = field.get("validation", {}) if isinstance(field, dict) else {}
            if isinstance(validation, dict) and "enum" in validation:
                kwargs["llm_candidate"] = None
            return await original_extract(self, field, **kwargs)

        with patch.object(FieldExtractionPipeline, "extract", new=skip_invalid_candidate):
            report = await run_benchmark_async()

        invalid = next(
            case
            for case in report["cases"]
            if case["case_id"] == "invalid-nonempty-candidate"
        )
        self.assertFalse(report["passed"])
        self.assertFalse(invalid["passed"])
        self.assertEqual(invalid["pipeline_stage"], "exhausted")
        self.assertEqual(report["metrics"]["fixture_candidate_calls"], 0)

    async def test_reported_episode_ids_are_created_in_the_experience_store(self) -> None:
        original_create = ExperienceStore.create_episode
        created_ids: list[str] = []

        def observe_create(store, **kwargs):
            episode = original_create(store, **kwargs)
            created_ids.append(episode.id)
            return episode

        with patch.object(ExperienceStore, "create_episode", new=observe_create):
            report = await run_benchmark_async()

        expected_ids = {case["repair_episode_id"] for case in report["cases"]}
        self.assertEqual(set(created_ids), expected_ids)
        self.assertEqual(len(created_ids), len(expected_ids) * 2)


if __name__ == "__main__":
    unittest.main()
