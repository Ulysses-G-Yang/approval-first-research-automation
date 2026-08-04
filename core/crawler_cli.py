"""Installed ``crawler`` command and source-checkout compatibility helpers."""

from __future__ import annotations

import argparse
import asyncio
import csv
import inspect
import io
import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Protocol

import yaml
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from core.captures import RequiredCaptureError
from core.experience_store import (
    EpisodeNotFoundError,
    ExperienceStore,
    ExperienceStoreError,
    PlanPatchError,
)


class BenchmarkRunner(Protocol):
    """Injectable boundary for the benchmark implementation."""

    def __call__(self, args: argparse.Namespace) -> Any | Awaitable[Any]: ...


SpiderFactory = Callable[[dict[str, Any]], Any]


def _default_benchmark_runner(args: argparse.Namespace) -> Any:
    from core.js_benchmark import benchmark_runner

    return benchmark_runner(args)


def _version() -> str:
    try:
        from research_assistant import __version__

        return __version__
    except Exception:  # pragma: no cover - installed metadata fallback
        try:
            from importlib.metadata import version

            return version("generic-crawler-research-assistant")
        except Exception:
            return "unknown"


def load_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Crawler configuration does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Crawler configuration must be a mapping: {path}")
    return data


def dump_json(records: Iterable[Mapping[str, Any]]) -> str:
    return json.dumps(list(records), ensure_ascii=False, indent=2)


def write_csv(records: Iterable[Mapping[str, Any]], path: Path) -> None:
    rows = [dict(row) for row in records]
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def enrich_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for row in records:
        row.setdefault("爬取时间", now)
        row.setdefault("crawl_time", now)
    return records


def output_records(records: list[dict[str, Any]], output: str | None, fmt: str) -> None:
    records = enrich_records(records)
    if not output:
        if fmt == "jsonl":
            print("\n".join(json.dumps(item, ensure_ascii=False) for item in records))
        elif fmt == "csv":
            if not records:
                print("")
                return
            headers: list[str] = []
            for row in records:
                for key in row:
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

    destination = Path(output).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    extension = destination.suffix.lower()
    if extension == ".jsonl":
        destination.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
            encoding="utf-8",
        )
    elif extension == ".csv":
        write_csv(records, destination)
    else:
        destination.write_text(dump_json(records), encoding="utf-8")


def _default_spider_factory(
    config: dict[str, Any],
    *,
    experience_store: ExperienceStore | None = None,
) -> Any:
    # Lazy import keeps episode inspection usable when browser extras are not
    # needed by the current command.
    from core.spider_engine import GenericSpider

    if experience_store is not None:
        try:
            parameters = inspect.signature(GenericSpider).parameters.values()
            if any(
                parameter.name == "experience_store" or parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            ):
                return GenericSpider(config, experience_store=experience_store)
        except (TypeError, ValueError):
            pass
    spider = GenericSpider(config)
    if experience_store is not None:
        setter = getattr(spider, "set_experience_store", None)
        if callable(setter):
            setter(experience_store)
        else:
            setattr(spider, "experience_store", experience_store)
    return spider


async def run_crawler(
    args: argparse.Namespace,
    *,
    spider_factory: SpiderFactory | None = None,
) -> list[dict[str, Any]]:
    config = load_config(args.config)
    if getattr(args, "start_url", None):
        config["start_url"] = args.start_url
        config.pop("start_urls", None)
    raw_start_urls = config.get("start_urls", []) or []
    if not isinstance(raw_start_urls, (list, tuple)):
        raise ValueError("start_urls must be a list of URLs.")
    configured_urls = [config.get("start_url"), *raw_start_urls]
    if not any(isinstance(value, str) and value.strip() for value in configured_urls):
        raise ValueError("No start URL configured. Add start_url or start_urls in config.")
    if getattr(args, "retain_full_episode_content", False):
        config["retain_full_episode_content"] = True
    factory = spider_factory or _default_spider_factory
    experience_store = None
    store_path = getattr(args, "experience_store", None)
    if store_path:
        experience_store = ExperienceStore(store_path)
    try:
        spider = _construct_spider(factory, config, experience_store)
        records = spider.run()
        if inspect.isawaitable(records):
            records = await records
    finally:
        if experience_store is not None:
            experience_store.close()
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError("GenericSpider.run() must return a list of record mappings.")
    return records


def _construct_spider(
    factory: SpiderFactory,
    config: dict[str, Any],
    experience_store: ExperienceStore | None,
) -> Any:
    if experience_store is None:
        return factory(config)
    accepts_store = False
    try:
        parameters = inspect.signature(factory).parameters.values()
        accepts_store = any(
            parameter.name == "experience_store" or parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        accepts_store = False
    if accepts_store:
        return factory(config, experience_store=experience_store)  # type: ignore[call-arg]

    spider = factory(config)
    setter = getattr(spider, "set_experience_store", None)
    if callable(setter):
        setter(experience_store)
    else:
        # Compatibility injection point for the current GenericSpider while its
        # constructor remains stable.  New implementations can accept the
        # keyword argument or expose set_experience_store().
        setattr(spider, "experience_store", experience_store)
    return spider


async def legacy_main(
    argv: Iterable[str] | None = None,
    *,
    spider_factory: SpiderFactory | None = None,
) -> None:
    """Async compatibility entry used by ``python extract_prices.py``."""

    args = parse_legacy_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    records = await run_crawler(args, spider_factory=spider_factory)
    output_records(records, args.output, args.format)


def parse_legacy_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="配置驱动爬虫入口")
    _add_run_arguments(parser)
    return parser.parse_args(list(argv) if argv is not None else None)


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--config",
        default="configs/taobao.yaml",
        help="Trusted crawler YAML path.",
    )
    parser.add_argument("--start-url", help="Override the configured start URL.")
    parser.add_argument(
        "--experience-store",
        help="Explicit RepairEpisode SQLite path; omitted means experience storage is disabled.",
    )
    parser.add_argument(
        "--retain-full-episode-content",
        action="store_true",
        help=(
            "Explicitly retain redacted full text/JSON captures for sources marked "
            "authorization_category=authorized; default storage is structure-only."
        ),
    )
    parser.add_argument("-o", "--output", help="Optional .json, .jsonl, or .csv output path.")
    parser.add_argument(
        "--format",
        choices=["json", "jsonl", "csv"],
        default="json",
        help="Terminal format when --output is omitted.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )


def _add_store_argument(parser: argparse.ArgumentParser, *, suppress_default: bool = False) -> None:
    kwargs: dict[str, Any] = {
        "dest": "experience_store",
        "help": "Explicit path to the opt-in RepairEpisode SQLite store.",
    }
    if suppress_default:
        kwargs["default"] = argparse.SUPPRESS
    parser.add_argument("--store", "--experience-store", **kwargs)


def _add_json_argument(parser: argparse.ArgumentParser, *, suppress_default: bool = False) -> None:
    kwargs: dict[str, Any] = {
        "dest": "json_output",
        "action": "store_true",
        "help": "Print machine-readable JSON.",
    }
    if suppress_default:
        kwargs["default"] = argparse.SUPPRESS
    parser.add_argument("--json", **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crawler",
        description="Run the configurable crawler, local benchmarks, and opt-in repair episodes.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run a trusted crawler YAML configuration.")
    _add_run_arguments(run)
    run.add_argument("--json", dest="run_json", action="store_true", help="Print JSON records.")

    benchmark = commands.add_parser(
        "benchmark", help="Run a benchmark through the injectable benchmark-runner boundary."
    )
    benchmark.add_argument("--suite", help="Optional benchmark suite or manifest path.")
    benchmark.add_argument("--fixture-root", help="Optional local fixture root.")
    benchmark.add_argument("--output", help="Optional benchmark result path.")
    benchmark.add_argument(
        "--check-baseline",
        action="store_true",
        help="Ask the benchmark runner to compare results with its declared baseline.",
    )
    _add_json_argument(benchmark)

    episodes = commands.add_parser("episodes", help="Inspect an explicitly enabled RepairEpisode store.")
    _add_store_argument(episodes)
    _add_json_argument(episodes)
    episode_commands = episodes.add_subparsers(dest="episodes_command", required=True)

    list_command = episode_commands.add_parser("list", help="List repair episodes.")
    list_command.add_argument("--limit", type=int, default=100)
    list_command.add_argument("--status")
    _add_store_argument(list_command, suppress_default=True)
    _add_json_argument(list_command, suppress_default=True)

    show = episode_commands.add_parser("show", help="Show one repair episode.")
    show.add_argument("episode_id")
    show.add_argument("--include-artifacts", action="store_true")
    _add_store_argument(show, suppress_default=True)
    _add_json_argument(show, suppress_default=True)

    export = episode_commands.add_parser("export", help="Export one repair episode as JSON.")
    export.add_argument("episode_id")
    export.add_argument("-o", "--output")
    export.add_argument("--include-artifacts", action="store_true")
    _add_store_argument(export, suppress_default=True)
    _add_json_argument(export, suppress_default=True)
    return parser


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    return str(value)


def _print_json(value: Any) -> None:
    print(json.dumps(_jsonable(value), ensure_ascii=False, indent=2))


def _command_run(args: argparse.Namespace, spider_factory: SpiderFactory | None) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    if args.run_json:
        args.format = "json"
    records = asyncio.run(run_crawler(args, spider_factory=spider_factory))
    output_records(records, args.output, args.format)
    return 0


def _command_benchmark(args: argparse.Namespace, runner: BenchmarkRunner | None) -> int:
    if runner is None:
        raise ExperienceStoreError(
            "No benchmark runner is wired. Pass benchmark_runner to core.crawler_cli.main "
            "or install the benchmark integration."
        )
    result = runner(args)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    payload = _jsonable(result)
    if args.output:
        destination = Path(args.output).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json_output:
        _print_json(payload)
    else:
        print("Crawler benchmark completed.")
        if args.output:
            print(f"Result: {Path(args.output).expanduser()}")
        else:
            _print_json(payload)
    if getattr(args, "check_baseline", False):
        if not isinstance(payload, Mapping) or payload.get("passed") is not True:
            return 1
    return 0


def _store_path(args: argparse.Namespace) -> str:
    value = getattr(args, "experience_store", None)
    if not value:
        raise ExperienceStoreError(
            "Episode storage is disabled by default; provide --store PATH explicitly."
        )
    return str(value)


def _command_episodes(args: argparse.Namespace) -> int:
    with ExperienceStore(_store_path(args)) as store:
        if args.episodes_command == "list":
            episodes = [episode.to_dict() for episode in store.list_episodes(limit=args.limit, status=args.status)]
            if args.json_output:
                _print_json(episodes)
            elif not episodes:
                print("No repair episodes.")
            else:
                for episode in episodes:
                    print(
                        f"{episode['id']}  {episode['status']}  "
                        f"{episode['authorization_category']}  {episode['created_at']}"
                    )
            return 0

        if args.episodes_command == "show":
            payload = store.get_episode(args.episode_id, include_artifacts=args.include_artifacts)
            if args.json_output:
                _print_json(payload)
            else:
                episode = payload["episode"]
                print(f"Episode: {episode['id']}")
                print(f"Status: {episode['status']}")
                print(f"Authorization: {episode['authorization_category']}")
                print(
                    f"Events: {len(payload['events'])}; proposals: {len(payload['proposals'])}; "
                    f"validations: {len(payload['validations'])}; decisions: {len(payload['decisions'])}"
                )
            return 0

        if args.episodes_command == "export":
            if args.output:
                destination = store.export_episode(
                    args.episode_id,
                    args.output,
                    include_artifacts=args.include_artifacts,
                )
                if args.json_output:
                    _print_json({"episode_id": args.episode_id, "output": str(destination)})
                else:
                    print(f"Exported repair episode: {destination}")
            else:
                payload = store.export_episode(
                    args.episode_id,
                    include_artifacts=args.include_artifacts,
                )
                _print_json(payload)
            return 0
    raise ExperienceStoreError(f"Unsupported episodes command: {args.episodes_command}")


def main(
    argv: Iterable[str] | None = None,
    *,
    benchmark_runner: BenchmarkRunner | None = None,
    spider_factory: SpiderFactory | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "run":
            return _command_run(args, spider_factory)
        if args.command == "benchmark":
            return _command_benchmark(args, benchmark_runner)
        if args.command == "episodes":
            return _command_episodes(args)
        parser.error(f"Unknown command: {args.command}")
    except (
        EpisodeNotFoundError,
        ExperienceStoreError,
        PlanPatchError,
        RequiredCaptureError,
        PlaywrightTimeoutError,
        asyncio.TimeoutError,
        FileNotFoundError,
        OSError,
        ValueError,
        yaml.YAMLError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "BenchmarkRunner",
    "build_parser",
    "dump_json",
    "enrich_records",
    "legacy_main",
    "load_config",
    "main",
    "output_records",
    "parse_legacy_args",
    "run_crawler",
    "write_csv",
]
