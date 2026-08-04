from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from core.crawler_cli import main
from core.captures import RequiredCaptureError
from core.experience_store import ExperienceStore


class FakeSpider:
    def __init__(self, config: dict[str, object]):
        self.config = config

    async def run(self) -> list[dict[str, object]]:
        return [{"name": self.config.get("name"), "url": self.config.get("start_url")}]


class StoreAwareSpider:
    def __init__(self, config: dict[str, object], *, experience_store: ExperienceStore):
        self.config = config
        self.store = experience_store

    async def run(self) -> list[dict[str, object]]:
        episode = self.store.create_episode(
            episode_id="run-episode",
            authorization_category="synthetic_local",
        )
        self.store.append_event(episode, "run", {"configured": True})
        return [{"stored": True}]


class CrawlerCliTests(unittest.TestCase):
    def test_run_uses_installed_command_shape_and_injected_spider(self) -> None:
        with TemporaryDirectory() as temp:
            config = Path(temp) / "crawler.yaml"
            config.write_text("name: fixture\nstart_url: https://fixture.invalid/\n", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    ["run", "--config", str(config), "--json"],
                    spider_factory=FakeSpider,
                )
        self.assertEqual(result, 0)
        records = json.loads(output.getvalue())
        self.assertEqual(records[0]["name"], "fixture")
        self.assertEqual(records[0]["url"], "https://fixture.invalid/")

    def test_start_url_override_replaces_the_entire_configured_url_set(self) -> None:
        received: list[dict[str, object]] = []

        def factory(config: dict[str, object]) -> FakeSpider:
            received.append(config)
            return FakeSpider(config)

        with TemporaryDirectory() as temp:
            config = Path(temp) / "crawler.yaml"
            config.write_text(
                "start_url: https://old.invalid/\n"
                "start_urls:\n"
                "  - https://old.invalid/\n"
                "  - https://second.invalid/\n",
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                result = main(
                    [
                        "run",
                        "--config",
                        str(config),
                        "--start-url",
                        "https://override.invalid/",
                        "--json",
                    ],
                    spider_factory=factory,
                )

        self.assertEqual(result, 0)
        self.assertEqual(received[0]["start_url"], "https://override.invalid/")
        self.assertNotIn("start_urls", received[0])

    def test_benchmark_runner_is_injectable(self) -> None:
        received: list[tuple[str | None, bool]] = []

        def runner(args):
            received.append((args.suite, args.check_baseline))
            return {"passed": True, "cases": 3}

        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                ["benchmark", "--suite", "local-fixtures", "--check-baseline", "--json"],
                benchmark_runner=runner,
            )
        self.assertEqual(result, 0)
        self.assertEqual(received, [("local-fixtures", True)])
        self.assertEqual(json.loads(output.getvalue())["cases"], 3)

    def test_benchmark_baseline_gate_fails_closed_on_malformed_results(self) -> None:
        for malformed in ({}, {"passed": None}, [], "unexpected"):
            with self.subTest(result=malformed), redirect_stdout(io.StringIO()):
                result = main(
                    ["benchmark", "--check-baseline", "--json"],
                    benchmark_runner=lambda _args, value=malformed: value,
                )
            self.assertEqual(result, 1)

    def test_run_store_is_opt_in_and_injected_without_breaking_old_factories(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "crawler.yaml"
            config.write_text("name: fixture\nstart_url: https://fixture.invalid/\n", encoding="utf-8")
            database = root / "experience.sqlite3"
            with redirect_stdout(io.StringIO()):
                result = main(
                    [
                        "run",
                        "--config",
                        str(config),
                        "--experience-store",
                        str(database),
                        "--json",
                    ],
                    spider_factory=StoreAwareSpider,
                )
            self.assertEqual(result, 0)
            self.assertTrue(database.is_file())
            self.assertTrue(database.with_name(f"{database.name}.cas").is_dir())
            with ExperienceStore(database) as store:
                self.assertEqual(store.list_episodes()[0].id, "run-episode")

            disabled_database = root / "not-created.sqlite3"
            with redirect_stdout(io.StringIO()):
                disabled = main(
                    ["run", "--config", str(config), "--json"],
                    spider_factory=FakeSpider,
                )
            self.assertEqual(disabled, 0)
            self.assertFalse(disabled_database.exists())

    def test_episodes_require_an_explicit_store_path(self) -> None:
        errors = io.StringIO()
        with redirect_stderr(errors):
            result = main(["episodes", "list", "--json"])
        self.assertEqual(result, 2)
        self.assertIn("disabled by default", errors.getvalue())

    def test_run_without_start_url_fails_cleanly_before_constructing_spider(self) -> None:
        with TemporaryDirectory() as temp:
            config = Path(temp) / "crawler.yaml"
            config.write_text("name: missing-url\n", encoding="utf-8")
            errors = io.StringIO()
            with redirect_stderr(errors):
                result = main(
                    ["run", "--config", str(config)],
                    spider_factory=lambda _config: self.fail("factory should not run"),
                )

        self.assertEqual(result, 2)
        self.assertIn("No start URL configured", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_run_rejects_string_start_urls_before_constructing_spider(self) -> None:
        with TemporaryDirectory() as temp:
            config = Path(temp) / "crawler.yaml"
            config.write_text(
                "name: malformed-urls\nstart_urls: https://fixture.invalid/\n",
                encoding="utf-8",
            )
            errors = io.StringIO()
            with redirect_stderr(errors):
                result = main(
                    ["run", "--config", str(config)],
                    spider_factory=lambda _config: self.fail("factory should not run"),
                )

        self.assertEqual(result, 2)
        self.assertIn("start_urls must be a list", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_required_capture_failure_returns_error_without_traceback(self) -> None:
        class CaptureFailureSpider:
            async def run(self):
                raise RequiredCaptureError("Required captures failed: catalog")

        with TemporaryDirectory() as temp:
            config = Path(temp) / "crawler.yaml"
            config.write_text("start_url: https://fixture.invalid/\n", encoding="utf-8")
            errors = io.StringIO()
            with redirect_stderr(errors):
                result = main(
                    ["run", "--config", str(config)],
                    spider_factory=lambda _config: CaptureFailureSpider(),
                )

        self.assertEqual(result, 2)
        self.assertIn("Required captures failed", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_episode_list_show_and_export_json(self) -> None:
        with TemporaryDirectory() as temp:
            database = Path(temp) / "experience.sqlite3"
            export = Path(temp) / "episode.json"
            with ExperienceStore(database) as store:
                episode = store.create_episode(
                    episode_id="episode-1",
                    authorization_category="synthetic_local",
                )
                store.append_event(episode, "failure", {"field": "title"})

            list_output = io.StringIO()
            with redirect_stdout(list_output):
                listed = main(["episodes", "list", "--store", str(database), "--json"])
            self.assertEqual(listed, 0)
            self.assertEqual(json.loads(list_output.getvalue())[0]["id"], "episode-1")

            show_output = io.StringIO()
            with redirect_stdout(show_output):
                shown = main(["episodes", "--store", str(database), "show", "episode-1", "--json"])
            self.assertEqual(shown, 0)
            self.assertEqual(json.loads(show_output.getvalue())["episode"]["id"], "episode-1")

            export_output = io.StringIO()
            with redirect_stdout(export_output):
                exported = main(
                    [
                        "episodes",
                        "export",
                        "episode-1",
                        "--store",
                        str(database),
                        "--output",
                        str(export),
                        "--json",
                    ]
                )
            self.assertEqual(exported, 0)
            self.assertTrue(export.is_file())
            self.assertEqual(json.loads(export.read_text(encoding="utf-8"))["episode"]["id"], "episode-1")
            self.assertEqual(json.loads(export_output.getvalue())["output"], str(export))


if __name__ == "__main__":
    unittest.main()
