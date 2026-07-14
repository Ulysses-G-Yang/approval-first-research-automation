from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import quote_plus, urljoin, urlparse

import yaml

from .models import Artifact, RiskLevel
from .registry import ToolContext, ToolError, ToolResult


MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_WEB_BYTES = 1 * 1024 * 1024
SUPPORTED_FILE_SUFFIXES = {".csv", ".json", ".md", ".markdown", ".txt"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str, fallback: str = "source") -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-").lower()
    return clean[:64] or fallback


def _filename(prefix: str, value: str, suffix: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{_slug(value)}-{digest}{suffix}"


def _strip_html(html: str) -> str:
    """Turn ordinary public-page HTML into reviewable plain text without executing it."""
    without_ignored = re.sub(
        r"<(script|style|noscript|svg)[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    without_tags = re.sub(r"<[^>]+>", " ", without_ignored)
    return re.sub(r"\s+", " ", without_tags).strip()


def _page_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    return _strip_html(match.group(1)) if match else ""


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return bool(ip.is_global)


def validate_public_url(url: str, resolver: Callable[..., Any] = socket.getaddrinfo) -> str:
    """Reject local/private targets before a network tool makes a request.

    This is deliberately conservative. The V1 assistant only reads public HTTP(S)
    pages and is not a gateway to a user's private network.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ToolError("Only public http(s) URLs without credentials are supported.")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        raise ToolError("Local network targets are not supported.")

    try:
        if _is_public_ip(hostname):
            return parsed.geturl()
    except ValueError:
        pass

    try:
        records = resolver(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ToolError(f"Could not resolve public URL host: {hostname}") from exc

    addresses = {record[4][0] for record in records}
    if not addresses or any(not _is_public_ip(address) for address in addresses):
        raise ToolError("URL resolves to a non-public network address.")
    return parsed.geturl()


def _looks_like_public_http_url(url: str) -> bool:
    """Perform a no-network URL check while parsing an explicit local URL list."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        return False
    try:
        return _is_public_ip(hostname)
    except ValueError:
        return True


async def _http_get(url: str, timeout_seconds: float, max_bytes: int) -> tuple[str, Dict[str, str], int, str]:
    try:
        import httpx
    except Exception as exc:  # pragma: no cover - dependency guard
        raise ToolError("httpx is required for web tools. Install requirements.txt first.") from exc

    current_url = validate_public_url(url)
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=False,
        headers={"User-Agent": "GenericCrawlerResearchAssistant/0.1 (+local approved task)"},
    ) as client:
        for _ in range(5):
            try:
                request = client.build_request("GET", current_url)
                response = await client.send(request, stream=True)
            except httpx.HTTPError as exc:
                raise ToolError(f"Web request failed: {exc}") from exc
            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ToolError("Redirect response did not include a location.")
                    current_url = validate_public_url(urljoin(current_url, location))
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise ToolError(f"Public web request returned an error: {exc}") from exc
                chunks: List[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > max_bytes:
                        raise ToolError(f"Response exceeded the {max_bytes} byte safety limit.")
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                return (
                    current_url,
                    dict(response.headers),
                    response.status_code,
                    b"".join(chunks).decode(encoding, errors="replace"),
                )
            except httpx.HTTPError as exc:
                raise ToolError(f"Public web response could not be read: {exc}") from exc
            finally:
                await response.aclose()
    raise ToolError("Too many redirects while reading the public URL.")


@dataclass
class WebFetchTool:
    name: str = "web.fetch"
    description: str = "Read one approved public HTTP(S) page and save normalized source text."
    risk: RiskLevel = RiskLevel.READ
    required_arguments: tuple[str, ...] = ("url",)
    allowed_arguments: tuple[str, ...] = ("url", "timeout_seconds")

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        url = validate_public_url(str(arguments["url"]))
        final_url, headers, status_code, html = await _http_get(
            url,
            timeout_seconds=float(arguments.get("timeout_seconds", 20)),
            max_bytes=MAX_WEB_BYTES,
        )
        content_type = headers.get("content-type", "")
        if "html" not in content_type.lower() and content_type:
            raise ToolError(f"web.fetch only accepts HTML pages, got content type: {content_type}")
        source = {
            "kind": "web",
            "url": final_url,
            "requested_url": url,
            "fetched_at": utc_now(),
            "status_code": status_code,
            "content_type": content_type,
            "title": _page_title(html),
            "text": _strip_html(html),
        }
        artifact = context.workspace.write_json_artifact(
            _filename("web", final_url, ".json"),
            source,
            kind="web_source",
            description=f"Public page fetched from {final_url}",
            source_url=final_url,
            metadata={"status_code": status_code, "content_type": content_type},
        )
        return ToolResult(
            summary=f"Read public page: {source['title'] or final_url}",
            artifacts=[artifact],
            details={"url": final_url, "network_target": urlparse(final_url).netloc},
        )


@dataclass
class WebSearchTool:
    name: str = "web.search"
    description: str = "Search the public web and save a small URL list for review."
    risk: RiskLevel = RiskLevel.READ
    required_arguments: tuple[str, ...] = ("query",)
    allowed_arguments: tuple[str, ...] = ("query", "limit")

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        query = str(arguments["query"]).strip()
        if not query:
            raise ToolError("web.search requires a non-empty query.")
        result_limit = min(max(int(arguments.get("limit", 5)), 1), 10)
        search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        final_url, _, _, html = await _http_get(search_url, timeout_seconds=20, max_bytes=MAX_WEB_BYTES)

        results: List[Dict[str, str]] = []
        for href, title in re.findall(
            r'<a[^>]+class=["\'][^"\']*result__a[^"\']*["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            candidate = urljoin(final_url, href)
            try:
                public_url = validate_public_url(candidate)
            except ToolError:
                continue
            results.append({"title": _strip_html(title), "url": public_url})
            if len(results) >= result_limit:
                break

        artifact = context.workspace.write_json_artifact(
            _filename("search", query, ".json"),
            {"kind": "search", "query": query, "searched_at": utc_now(), "results": results},
            kind="url_list",
            description=f"Search results for: {query}",
            source_url=final_url,
            metadata={"query": query, "count": len(results)},
        )
        return ToolResult(
            summary=f"Found {len(results)} reviewable public URLs for: {query}",
            artifacts=[artifact],
            details={"network_target": urlparse(final_url).netloc, "result_count": len(results)},
        )


@dataclass
class FileReadTool:
    name: str = "file.read"
    description: str = "Read one explicitly supplied Markdown, TXT, CSV, or JSON file."
    risk: RiskLevel = RiskLevel.READ
    required_arguments: tuple[str, ...] = ("path",)
    allowed_arguments: tuple[str, ...] = ("path",)

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        path = context.resolve_input(str(arguments["path"]))
        if path.suffix.lower() not in SUPPORTED_FILE_SUFFIXES:
            raise ToolError("Supported local files are Markdown, TXT, CSV, and JSON only.")
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ToolError(f"Input file exceeded the {MAX_INPUT_BYTES} byte safety limit.")

        text = path.read_text(encoding="utf-8-sig")
        source: Dict[str, Any] = {
            "kind": "file",
            "path": str(path),
            "read_at": utc_now(),
            "format": path.suffix.lower().lstrip("."),
            "title": path.stem,
            "text": "",
            "records": [],
        }
        if path.suffix.lower() == ".csv":
            source["records"] = list(csv.DictReader(text.splitlines()))
            source["text"] = text
        elif path.suffix.lower() == ".json":
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ToolError(f"Input JSON is invalid: {path}") from exc
            source["records"] = value if isinstance(value, list) else [value]
            source["text"] = json.dumps(value, ensure_ascii=False, indent=2)
        else:
            source["text"] = text

        artifact = context.workspace.write_json_artifact(
            _filename("file", str(path), ".json"),
            source,
            kind="file_source",
            description=f"Explicitly approved local file: {path.name}",
            metadata={"input_path": str(path), "format": source["format"]},
        )
        return ToolResult(
            summary=f"Read input file: {path.name}",
            artifacts=[artifact],
            details={"input_path": str(path), "bytes": path.stat().st_size},
        )


def _urls_from_json(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _urls_from_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _urls_from_json(child)


@dataclass
class UrlListReadTool:
    name: str = "url_list.read"
    description: str = "Read public HTTP(S) URLs from one explicitly supplied TXT, Markdown, CSV, or JSON file."
    risk: RiskLevel = RiskLevel.READ
    required_arguments: tuple[str, ...] = ("path",)
    allowed_arguments: tuple[str, ...] = ("path", "max_urls")

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        path = context.resolve_input(str(arguments["path"]))
        if path.suffix.lower() not in SUPPORTED_FILE_SUFFIXES:
            raise ToolError("URL list input must be Markdown, TXT, CSV, or JSON.")
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ToolError(f"Input file exceeded the {MAX_INPUT_BYTES} byte safety limit.")
        text = path.read_text(encoding="utf-8-sig")
        candidates: List[str] = []
        suffix = path.suffix.lower()
        if suffix == ".csv":
            for row in csv.reader(text.splitlines()):
                candidates.extend(row)
        elif suffix == ".json":
            try:
                candidates.extend(_urls_from_json(json.loads(text)))
            except json.JSONDecodeError as exc:
                raise ToolError(f"Input JSON is invalid: {path}") from exc
        else:
            candidates.extend(re.findall(r"https?://[^\s<>()\[\]{}\"']+", text, flags=re.IGNORECASE))

        max_urls = min(max(int(arguments.get("max_urls", 50)), 1), 200)
        urls: List[str] = []
        for candidate in candidates:
            clean = str(candidate).strip().rstrip(".,;:!?")
            if _looks_like_public_http_url(clean) and clean not in urls:
                urls.append(clean)
            if len(urls) >= max_urls:
                break
        artifact = context.workspace.write_json_artifact(
            _filename("url-list", str(path), ".json"),
            {
                "kind": "url_list",
                "title": path.name,
                "path": str(path),
                "read_at": utc_now(),
                "urls": urls,
            },
            kind="url_list",
            description=f"Public URL list from explicitly approved file: {path.name}",
            metadata={"input_path": str(path), "count": len(urls)},
        )
        return ToolResult(
            summary=f"Read {len(urls)} public URL candidates from {path.name}.",
            artifacts=[artifact],
            details={"input_path": str(path), "url_count": len(urls)},
        )


def _source_records(artifact: Artifact, value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, list):
        for record in value:
            if isinstance(record, dict):
                yield {**record, "_source": artifact.metadata.get("config_path", ""), "_artifact_id": artifact.id}
            else:
                yield {"value": record, "_source": artifact.metadata.get("config_path", ""), "_artifact_id": artifact.id}
        return
    if not isinstance(value, dict):
        return

    source_url = value.get("url") or artifact.source_url
    if value.get("kind") in {"search", "url_list"}:
        results = value.get("results") or value.get("urls") or []
        for result in results:
            if isinstance(result, str):
                result = {"url": result, "title": ""}
            if isinstance(result, dict):
                yield {
                    "title": result.get("title", ""),
                    "url": result.get("url", ""),
                    "_source": source_url or "public web search",
                    "_artifact_id": artifact.id,
                }
        return
    records = value.get("records")
    if isinstance(records, list) and records:
        for record in records:
            if isinstance(record, dict):
                yield {**record, "_source": source_url or value.get("path", ""), "_artifact_id": artifact.id}
            else:
                yield {"value": record, "_source": source_url or value.get("path", ""), "_artifact_id": artifact.id}
        return
    text = str(value.get("text", "")).strip()
    if text:
        yield {
            "title": value.get("title", ""),
            "text": text,
            "_source": source_url or value.get("path", ""),
            "_artifact_id": artifact.id,
        }


@dataclass
class DataNormalizeTool:
    name: str = "data.normalize"
    description: str = "Deduplicate approved source artifacts into a reusable structured dataset."
    risk: RiskLevel = RiskLevel.WRITE
    required_arguments: tuple[str, ...] = ()
    allowed_arguments: tuple[str, ...] = ()

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        source_artifacts = (
            context.artifacts("web_source")
            + context.artifacts("file_source")
            + context.artifacts("url_list")
            + context.artifacts("crawler_records")
        )
        records: List[Dict[str, Any]] = []
        seen: Dict[str, Dict[str, Any]] = {}
        for artifact in source_artifacts:
            value = context.workspace.read_artifact_json(artifact)
            for record in _source_records(artifact, value):
                semantic_record = {key: item for key, item in record.items() if not key.startswith("_")}
                fingerprint = json.dumps(semantic_record, ensure_ascii=False, sort_keys=True)
                digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
                if digest in seen:
                    existing_sources = seen[digest].setdefault("_sources", [])
                    source = record.get("_source", "")
                    if source and source not in existing_sources:
                        existing_sources.append(source)
                    continue
                source = record.get("_source", "")
                record["_sources"] = [source] if source else []
                seen[digest] = record
                records.append(record)

        dataset = {
            "created_at": utc_now(),
            "goal": context.task.goal,
            "record_count": len(records),
            "source_artifact_ids": [artifact.id for artifact in source_artifacts],
            "records": records,
        }
        artifact = context.workspace.write_json_artifact(
            "normalized-dataset.json",
            dataset,
            kind="dataset",
            description="Deduplicated structured dataset from approved sources",
            metadata={"record_count": len(records)},
        )
        return ToolResult(
            summary=f"Normalized {len(records)} records from {len(source_artifacts)} approved source artifacts.",
            artifacts=[artifact],
            details={"record_count": len(records), "source_count": len(source_artifacts)},
        )


def _markdown_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return re.sub(r"\s+", " ", text).replace("|", "\\|").strip()


@dataclass
class MarkdownTableTool:
    name: str = "data.to_markdown"
    description: str = "Turn the normalized dataset into a reviewable Markdown table."
    risk: RiskLevel = RiskLevel.WRITE
    required_arguments: tuple[str, ...] = ()
    allowed_arguments: tuple[str, ...] = ("max_rows",)

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        datasets = context.artifacts("dataset")
        if not datasets:
            raise ToolError("data.to_markdown requires a completed data.normalize step.")
        dataset = context.workspace.read_artifact_json(datasets[-1])
        records = dataset.get("records", []) if isinstance(dataset, dict) else []
        if not isinstance(records, list):
            records = []
        max_rows = min(max(int(arguments.get("max_rows", 50)), 1), 200)
        visible = [record for record in records[:max_rows] if isinstance(record, dict)]
        columns: List[str] = []
        for record in visible:
            for key in record:
                if key not in columns and not key.startswith("_"):
                    columns.append(key)
        columns = columns[:12] or ["value"]
        lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
        for record in visible:
            lines.append("| " + " | ".join(_markdown_cell(record.get(column, "")) for column in columns) + " |")
        markdown = "\n".join(lines) + "\n"
        artifact = context.workspace.write_text_artifact(
            "dataset-table.md",
            markdown,
            kind="markdown_table",
            description="Markdown table generated from the normalized dataset",
            metadata={"shown_rows": len(visible), "total_records": len(records)},
        )
        return ToolResult(
            summary=f"Rendered {len(visible)} of {len(records)} records as Markdown.",
            artifacts=[artifact],
            details={"shown_rows": len(visible), "total_records": len(records)},
        )


def _deterministic_report(task_goal: str, dataset: Dict[str, Any], sources: List[Dict[str, Any]]) -> str:
    records = dataset.get("records", []) if isinstance(dataset, dict) else []
    count = len(records) if isinstance(records, list) else 0
    lines = [
        f"# {task_goal}",
        "",
        "## 摘要",
        "",
        f"本报告基于 {len(sources)} 个已批准来源整理，共得到 {count} 条去重记录。",
        "模型摘要未启用或不可用，因此本节仅陈述可复核的处理结果。",
        "",
        "## 来源",
        "",
    ]
    for source in sources:
        label = source.get("title") or source.get("path") or source.get("url") or "未命名来源"
        location = source.get("url") or source.get("path") or ""
        fetched = source.get("fetched_at") or source.get("read_at") or ""
        lines.append(f"- {label}: {location}（处理时间：{fetched}）")
    lines.extend(["", "## 数据说明", "", "原始来源、清洗后的数据集和本报告均位于同一任务工作区，可逐项核对。", ""])
    return "\n".join(lines)


async def _model_summary(context: ToolContext, dataset: Dict[str, Any], sources: List[Dict[str, Any]]) -> str:
    if context.provider is None:
        return ""
    records = dataset.get("records", []) if isinstance(dataset, dict) else []
    raw_sample = records[:10] if isinstance(records, list) else []
    sample: List[Dict[str, Any]] = []
    for record in raw_sample:
        if not isinstance(record, dict):
            continue
        clipped: Dict[str, Any] = {}
        for key, value in record.items():
            if isinstance(value, str):
                clipped[key] = value[:2000]
            else:
                clipped[key] = value
        sample.append(clipped)
    prompt = {
        "goal": context.task.goal,
        "record_count": len(records) if isinstance(records, list) else 0,
        "sources": [{"title": source.get("title", ""), "url": source.get("url", "")} for source in sources],
        "records_sample": sample,
        "sample_limits": "At most 10 records; each text value is clipped to 2,000 characters.",
    }
    system = (
        "You summarize only the supplied, already-approved research data. "
        "Do not request tools, execute code, invent citations, or include secrets. "
        "Write concise Markdown in Chinese with a factual summary and limits."
    )
    try:
        return (await context.provider.complete(system, json.dumps(prompt, ensure_ascii=False))).strip()
    except Exception as exc:
        context.workspace.append_log("model_summary_unavailable", {"error": str(exc)})
        return ""


def _collect_sources(context: ToolContext) -> List[Dict[str, Any]]:
    source_artifacts = (
        context.artifacts("web_source")
        + context.artifacts("file_source")
        + context.artifacts("url_list")
        + context.artifacts("crawler_records")
    )
    sources: List[Dict[str, Any]] = []
    for artifact in source_artifacts:
        source = context.workspace.read_artifact_json(artifact)
        if isinstance(source, dict):
            sources.append(
                {
                    "artifact_id": artifact.id,
                    "title": source.get("title", "") or source.get("query", ""),
                    "url": source.get("url", "") or artifact.source_url or "",
                    "path": source.get("path", "") or artifact.metadata.get("config_path", ""),
                    "fetched_at": source.get("fetched_at", "") or source.get("searched_at", ""),
                    "read_at": source.get("read_at", ""),
                    "field_origin": artifact.path,
                }
            )
        elif isinstance(source, list):
            sources.append(
                {
                    "artifact_id": artifact.id,
                    "title": f"Crawler output ({len(source)} records)",
                    "url": "",
                    "path": artifact.metadata.get("config_path", ""),
                    "fetched_at": "",
                    "read_at": "",
                    "field_origin": artifact.path,
                }
            )
    return sources


@dataclass
class ReportSummarizeTool:
    name: str = "report.summarize"
    description: str = "Send already-approved normalized data to the configured model and save a Markdown summary."
    risk: RiskLevel = RiskLevel.SENSITIVE
    required_arguments: tuple[str, ...] = ()
    allowed_arguments: tuple[str, ...] = ()

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        datasets = context.artifacts("dataset")
        if not datasets:
            raise ToolError("report.summarize requires a completed data.normalize step.")
        dataset = context.workspace.read_artifact_json(datasets[-1])
        sources = _collect_sources(context)
        summary = await _model_summary(context, dataset if isinstance(dataset, dict) else {}, sources)
        artifact = context.workspace.write_text_artifact(
            "model-summary.md",
            summary,
            kind="model_summary",
            description="Model summary of already-approved task data",
            metadata={"available": bool(summary), "provider": context.task.provider_name or ""},
        )
        if summary:
            return ToolResult("Created model summary from approved data.", artifacts=[artifact])
        return ToolResult(
            "Model summary unavailable; the later report step will use deterministic local output.",
            artifacts=[artifact],
        )


@dataclass
class ReportComposeTool:
    name: str = "report.compose"
    description: str = "Create a traceable Markdown report plus a JSONL source manifest locally."
    risk: RiskLevel = RiskLevel.WRITE
    required_arguments: tuple[str, ...] = ()
    allowed_arguments: tuple[str, ...] = ()

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        datasets = context.artifacts("dataset")
        if not datasets:
            raise ToolError("report.compose requires a completed data.normalize step.")
        dataset = context.workspace.read_artifact_json(datasets[-1])
        sources = _collect_sources(context)
        summaries = context.artifacts("model_summary")
        model_markdown = context.workspace.read_artifact_text(summaries[-1]).strip() if summaries else ""
        report = _deterministic_report(context.task.goal, dataset if isinstance(dataset, dict) else {}, sources)
        if model_markdown:
            report = report.replace("## 来源", f"## 模型整理摘要\n\n{model_markdown}\n\n## 来源")
        report_artifact = context.workspace.write_text_artifact(
            "report.md",
            report,
            kind="report",
            description="Traceable Markdown research report",
            metadata={"source_count": len(sources), "used_model_summary": bool(model_markdown)},
        )
        sources_artifact = context.workspace.write_text_artifact(
            "sources.jsonl",
            "".join(json.dumps(source, ensure_ascii=False) + "\n" for source in sources),
            kind="sources_manifest",
            description="Source URL and artifact manifest for the research report",
            metadata={"source_count": len(sources)},
        )
        return ToolResult("Created traceable Markdown report.", artifacts=[report_artifact, sources_artifact])


@dataclass
class BrowserExtractTool:
    """Expose the existing YAML crawler through the task approval boundary."""

    name: str = "browser.extract"
    description: str = "Run an explicitly supplied, reviewable crawler YAML against its configured public URLs."
    risk: RiskLevel = RiskLevel.READ
    required_arguments: tuple[str, ...] = ("config_path",)
    allowed_arguments: tuple[str, ...] = ("config_path",)

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        path = context.resolve_input(str(arguments["config_path"]))
        if path.suffix.lower() not in {".yaml", ".yml"}:
            raise ToolError("browser.extract requires an explicitly supplied YAML config.")
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(config, dict):
            raise ToolError("Crawler config must be a YAML mapping.")
        if config.get("actions"):
            raise ToolError("Agent browser.extract does not permit YAML actions in V1.")
        llm = config.get("llm")
        if isinstance(llm, dict) and llm.get("api_key"):
            raise ToolError("Crawler configs used by the agent must not contain an API key.")
        urls = [config.get("start_url"), *(config.get("start_urls") or [])]
        for url in urls:
            if url:
                validate_public_url(str(url))

        from core.spider_engine import GenericSpider

        records = await GenericSpider(config).run()
        artifact = context.workspace.write_json_artifact(
            _filename("crawl", str(path), ".json"),
            records,
            kind="crawler_records",
            description=f"Crawler records from approved config: {path.name}",
            metadata={"config_path": str(path), "record_count": len(records)},
        )
        return ToolResult(
            summary=f"Crawler completed with {len(records)} records.",
            artifacts=[artifact],
            details={"config_path": str(path), "record_count": len(records)},
        )


def build_default_registry() -> "ToolRegistry":
    from .registry import ToolRegistry
    from .content_tools import ContentPrepareDraftTool
    from .document_tools import DocumentConvertTool, DocumentInspectTool, MarkdownValidateTool

    registry = ToolRegistry()
    for tool in (
        WebFetchTool(),
        WebSearchTool(),
        FileReadTool(),
        UrlListReadTool(),
        BrowserExtractTool(),
        DocumentInspectTool(),
        DocumentConvertTool(),
        MarkdownValidateTool(),
        ContentPrepareDraftTool(),
        DataNormalizeTool(),
        MarkdownTableTool(),
        ReportSummarizeTool(),
        ReportComposeTool(),
    ):
        registry.register(tool)
    return registry


from .content_tools import ContentPrepareDraftTool
from .document_tools import DocumentConvertTool, DocumentInspectTool, MarkdownValidateTool

__all__ = [
    "BrowserExtractTool",
    "ContentPrepareDraftTool",
    "DataNormalizeTool",
    "DocumentConvertTool",
    "DocumentInspectTool",
    "FileReadTool",
    "MarkdownTableTool",
    "MarkdownValidateTool",
    "ReportComposeTool",
    "ReportSummarizeTool",
    "UrlListReadTool",
    "WebFetchTool",
    "WebSearchTool",
    "build_default_registry",
    "validate_public_url",
]
