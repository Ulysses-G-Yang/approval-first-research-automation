"""Local RepairEpisode v1 storage with privacy-safe, content-addressed artifacts.

The experience store is deliberately opt-in: :class:`ExperienceStore` has no
default path and no crawler component constructs it implicitly.  Callers must
choose a SQLite path explicitly.

The v1 record is an append-oriented audit bundle made of an episode, ordered
events, candidate proposals, validation results, and decisions. Captures are
stored in a SHA-256 content-addressed store (CAS). Full capture text is retained
for ``synthetic_local`` fixtures and may be explicitly enabled for
``authorized`` sources; secret redaction still applies. Other authorization
categories store a structural summary.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExperienceStoreError(RuntimeError):
    """Base error for the local experience store."""


class EpisodeNotFoundError(ExperienceStoreError):
    """Raised when an episode id does not exist in the store."""


class PlanPatchError(ValueError):
    """Raised when a repair proposal attempts to change a forbidden setting."""


class AuthorizationCategory(str, Enum):
    """Capture authorization categories understood by the v1 policy."""

    SYNTHETIC_LOCAL = "synthetic_local"
    PUBLIC = "public"
    AUTHORIZED = "authorized"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RepairEpisode:
    id: str
    schema_version: int
    created_at: str
    updated_at: str
    authorization_category: str
    source_url: str = ""
    page_pattern: str = ""
    retain_full_content: bool = False
    status: str = "open"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def episode_id(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepairEvent:
    id: int
    episode_id: str
    sequence: int
    created_at: str
    event_type: str
    payload: Any
    artifact_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepairProposal:
    id: str
    episode_id: str
    created_at: str
    source: str
    patch: Any
    rationale: str
    status: str
    historical: bool

    @property
    def proposal_id(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepairValidation:
    id: str
    episode_id: str
    proposal_id: str | None
    created_at: str
    passed: bool
    validator: str
    checks: Any
    metrics: Any
    evidence_sha256: str | None = None

    @property
    def validation_id(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepairDecision:
    id: str
    episode_id: str
    proposal_id: str | None
    created_at: str
    outcome: str
    actor: str
    reason: str
    metadata: Any

    @property
    def decision_id(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CASObject:
    sha256: str
    source_sha256: str
    size_bytes: int
    media_type: str
    storage_mode: str
    object_path: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LegacyImportResult:
    imported: int = 0
    skipped: int = 0
    invalid: int = 0
    episode_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["episode_ids"] = list(self.episode_ids)
        return result


_ALLOWED_PATCH_ROOTS = frozenset(
    {
        "fields",
        "captures",
        "validation",
        "request",
    }
)

_FORBIDDEN_PATCH_KEYS = frozenset(
    {
        "start_url",
        "start_urls",
        "url",
        "urls",
        "browser",
        "actions",
        "action",
        "credentials",
        "credential",
        "cookie",
        "cookies",
        "headers",
        "authorization",
        "auth",
        "api_key",
        "apikey",
        "secret",
        "secrets",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "profile",
        "browser_profile",
        "user_data_dir",
        "localstorage",
        "local_storage",
        "storage_state",
        "llm",
        "model",
        "provider",
        "endpoint",
        "base_url",
        "proxy",
    }
)

_SENSITIVE_PAYLOAD_KEYS = frozenset(
    {
        "authorization",
        "auth",
        "auth_header",
        "proxy_authorization",
        "cookie",
        "cookies",
        "cookie_jar",
        "credentials",
        "credential",
        "api_key",
        "apikey",
        "secret",
        "secrets",
        "client_secret",
        "password",
        "passwd",
        "token",
        "access_token",
        "refresh_token",
        "session_token",
        "csrf",
        "csrf_token",
        "xsrf",
        "xsrf_token",
        "profile",
        "browser_profile",
        "user_data_dir",
        "localstorage",
        "local_storage",
        "sessionstorage",
        "session_storage",
        "storage_state",
    }
)

_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "key",
        "token",
        "access_token",
        "refresh_token",
        "auth",
        "authorization",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "session",
        "sessionid",
        "csrf",
        "csrf_token",
        "xsrf",
        "xsrf_token",
    }
)

_CAPTURE_PAYLOAD_KEYS = frozenset(
    {
        "body",
        "capture",
        "content",
        "dom",
        "dom_snapshot",
        "extracted_text",
        "extracted_value",
        "html",
        "image",
        "page_html",
        "page_source",
        "raw",
        "request_body",
        "response_body",
        "screenshot",
        "text_content",
        "value_sample",
    }
)

_TEXT_SECRET_PATTERNS = (
    re.compile(
        r"(?is)<input\b(?=[^>]*(?:type\s*=\s*[\"']password[\"']|"
        r"name\s*=\s*[\"'][^\"']*(?:token|secret|auth|session|csrf|password|passwd)"
        r"[^\"']*[\"']))[^>]*>"
    ),
    re.compile(r"(?i)https?://[^/\s:@<>\"']+:[^@\s/<>\"']+@"),
    re.compile(
        r"(?is)(?:local|session)Storage\.setItem\([^)]*\)"
    ),
    re.compile(
        r"(?is)(?:local|session)Storage(?:\s*\[[^\]]+\]|\.[A-Za-z_$][\w$]*)"
        r"\s*=\s*[^;\r\n<]+"
    ),
    re.compile(
        r"(?is)<meta\b(?=[^>]*(?:name|http-equiv)\s*=\s*[\"']"
        r"(?:authorization|proxy-authorization|cookie|api-key|auth-token|session-token|"
        r"(?:csrf|xsrf)(?:[-_]?token)?)[\"'])[^>]*>"
    ),
    re.compile(
        r"(?im)([\"']?(?:authorization(?:[-_]?header)?|"
        r"proxy[-_]?authorization(?:[-_]?header)?)[\"']?\s*[:=]\s*)[^,}\r\n]+"
    ),
    re.compile(
        r"(?im)([\"']?(?:set[-_]?cookie|cookie(?:[-_]?header)?)[\"']?\s*[:=]\s*)"
        r"[^,}\r\n]+"
    ),
    re.compile(
        r"(?i)([\"']?(?:api[-_]?key|client[-_]?secret|access[-_]?token|refresh[-_]?token|"
        r"auth[-_]?token|id[-_]?token|session[-_]?token|"
        r"(?:csrf|xsrf)(?:[-_]?token)?|password|passwd)[\"']?\s*[:=]\s*)"
        r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^,;}\r\n]+)"
    ),
    re.compile(r"(?i)([?&](?:api[_-]?key|token|access_token|secret|password|auth)=)[^&#\s]+"),
    re.compile(
        r"(?i)([\"']?(?:local[-_]?storage|session[-_]?storage|browser[-_]?profile|"
        r"user[-_]?data[-_]?dir|storage[-_]?state)[\"']?\s*[:=]\s*)[^,}\r\n]+"
    ),
    re.compile(
        r"(?i)([\"']?(?:session[_-]?id|session[_-]?token)[\"']?\s*[:=]\s*)"
        r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^,;}\r\n]+)"
    ),
)


def _normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalize_key(key)
    if normalized == "authorization_category" or normalized.endswith(
        "_authorization_category"
    ):
        return False
    if normalized in _SENSITIVE_PAYLOAD_KEYS:
        return True

    # Configuration and serialized payloads commonly use camelCase or append
    # words such as ``Header``. Compare a separator-free form as well and keep
    # the policy deliberately conservative: these values must never enter the
    # episode database or content-addressed artifacts.
    compact = normalized.replace("_", "")
    compact_aliases = {
        candidate.replace("_", "") for candidate in _SENSITIVE_PAYLOAD_KEYS
    }
    if compact in compact_aliases:
        return True
    if compact.endswith(("token", "secret", "password", "passwd")):
        return True
    return any(
        marker in compact
        for marker in (
            "authorizationheader",
            "proxyauthorization",
            "cookieheader",
            "cookiejar",
            "browserprofile",
            "userdatadir",
            "localstorage",
            "sessionstorage",
            "storagestate",
        )
    )


def _redact_text(value: str) -> tuple[str, bool]:
    text = value
    changed = False
    for pattern in _TEXT_SECRET_PATTERNS:
        replacement = "[REDACTED]"
        if pattern.groups:
            replacement = r"\1[REDACTED]" if pattern.groups == 1 else "[REDACTED]"
        updated = pattern.sub(replacement, text)
        if updated != text:
            text = updated
            changed = True
    return text, changed


def _sanitize_url(value: str) -> str:
    if not value:
        return ""
    try:
        split = urlsplit(value)
    except ValueError:
        return ""
    if not split.scheme or not split.netloc:
        return _redact_text(value)[0]

    hostname = split.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    try:
        port = split.port
    except ValueError:
        return ""
    if port is not None:
        netloc = f"{netloc}:{port}"

    query = []
    for key, item in parse_qsl(split.query, keep_blank_values=True):
        sensitive = _normalize_key(key) in _SENSITIVE_QUERY_KEYS or _is_sensitive_key(key)
        query.append((key, "[REDACTED]" if sensitive else item))
    return urlunsplit((split.scheme, netloc, split.path, urlencode(query), ""))


def sanitize_source_url(value: Any) -> str:
    """Return a credential-free URL suitable for local audit persistence."""

    return _sanitize_url(str(value or ""))


def sanitize_payload(value: Any) -> Any:
    """Return a JSON-safe copy with credential/session material removed.

    Forbidden keyed values are omitted entirely.  Common header, query-string,
    and serialized-secret patterns in free text are replaced before persistence.
    """

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        return _redact_text(value)[0]
    if isinstance(value, bytes):
        return {"binary": True, "size_bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        redacted_fields: list[str] = []
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                redacted_fields.append(_normalize_key(key_text))
                continue
            result[key_text] = sanitize_payload(item)
        if redacted_fields:
            result["_redacted_fields"] = sorted(set(redacted_fields))
        return result
    if isinstance(value, Sequence):
        return [sanitize_payload(item) for item in value]
    return _redact_text(str(value))[0]


def _is_synthetic_local(category: str | AuthorizationCategory) -> bool:
    normalized = (
        category.value
        if isinstance(category, AuthorizationCategory)
        else str(category).strip().lower().replace("-", "_").replace(" ", "_")
    )
    return normalized in {"synthetic_local", "local_synthetic", "synthetic_fixture", "local_fixture"}


def _capture_value_summary(value: Any, key: str) -> dict[str, Any]:
    if isinstance(value, bytes):
        raw = value
        media_type = "application/octet-stream"
    elif isinstance(value, str):
        raw = value.encode("utf-8")
        media_type = "text/html" if "html" in key or key in {"dom", "page_source"} else "text/plain"
    else:
        raw = _json_dump(sanitize_payload(value)).encode("utf-8")
        media_type = "application/json"
    return _structural_summary(raw, media_type)


def sanitize_episode_payload(value: Any, authorization_category: str | AuthorizationCategory) -> Any:
    """Apply secret redaction plus the episode capture-retention policy."""

    if _is_synthetic_local(authorization_category):
        return sanitize_payload(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        redacted_fields: list[str] = []
        for key, item in value.items():
            key_text = str(key)
            normalized = _normalize_key(key_text)
            if _is_sensitive_key(normalized):
                redacted_fields.append(normalized)
                continue
            if normalized in _CAPTURE_PAYLOAD_KEYS:
                result[key_text] = _capture_value_summary(item, normalized)
            else:
                result[key_text] = sanitize_episode_payload(item, authorization_category)
        if redacted_fields:
            result["_redacted_fields"] = sorted(set(redacted_fields))
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_episode_payload(item, authorization_category) for item in value]
    return sanitize_payload(value)


def _walk_patch_keys(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _normalize_key(key)
            if normalized in _FORBIDDEN_PATCH_KEYS or _is_sensitive_key(normalized):
                location = "/".join((*path, str(key)))
                raise PlanPatchError(f"Repair patches may not change '{location}'.")
            _walk_patch_keys(item, (*path, str(key)))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _walk_patch_keys(item, (*path, str(index)))


def _validate_wait_value(key: str, value: Any) -> None:
    if key == "wait_until":
        allowed = {"load", "domcontentloaded", "networkidle", "commit"}
        if value not in allowed:
            raise PlanPatchError(f"wait_until must be one of {sorted(allowed)}.")
    elif key == "wait_for_selector":
        if not isinstance(value, str) or not value.strip() or len(value) > 1000:
            raise PlanPatchError("wait_for_selector must be a non-empty selector string.")
    elif key in {"timeout", "timeout_ms", "wait_timeout", "wait_for_timeout"}:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 300_000:
            raise PlanPatchError(f"{key} must be an integer between 1 and 300000.")


def validate_plan_patch(patch: Any) -> Any:
    """Validate and copy an allowlisted repair-plan patch.

    Mapping patches may only change ``fields``, ``captures``, ``validation``,
    or wait controls nested under ``request``.  RFC 6902-style operation lists are also
    accepted, but only ``add``, ``remove``, ``replace``, and ``test`` operations
    under the same allowlisted roots are permitted.  URL targets, browser
    configuration, scripted actions, credentials, profiles, and LLM endpoints
    are rejected at any nesting level.
    """

    if isinstance(patch, Mapping):
        if not patch:
            raise PlanPatchError("A repair patch cannot be empty.")
        for key, value in patch.items():
            normalized = _normalize_key(key)
            if normalized not in _ALLOWED_PATCH_ROOTS:
                raise PlanPatchError(f"Repair patches may not change top-level key '{key}'.")
            if normalized == "request":
                if not isinstance(value, Mapping) or not value:
                    raise PlanPatchError("request repair patches must be a non-empty object.")
                allowed_request_keys = {"wait_until", "wait_for_selector", "timeout_ms"}
                for request_key, request_value in value.items():
                    normalized_request_key = _normalize_key(request_key)
                    if normalized_request_key not in allowed_request_keys:
                        raise PlanPatchError(
                            f"Repair patches may not change request.{request_key}."
                        )
                    _validate_wait_value(normalized_request_key, request_value)
        _walk_patch_keys(patch)
        return copy.deepcopy(dict(patch))

    if isinstance(patch, Sequence) and not isinstance(patch, (str, bytes, bytearray)):
        if not patch:
            raise PlanPatchError("A repair patch cannot be empty.")
        result: list[dict[str, Any]] = []
        for index, operation in enumerate(patch):
            if not isinstance(operation, Mapping):
                raise PlanPatchError(f"JSON patch operation {index} must be an object.")
            op = str(operation.get("op", "")).lower()
            if op not in {"add", "remove", "replace", "test"}:
                raise PlanPatchError(f"JSON patch operation '{op}' is not allowed.")
            path = str(operation.get("path", ""))
            segments = [segment.replace("~1", "/").replace("~0", "~") for segment in path.split("/")[1:]]
            if not path.startswith("/") or not segments or _normalize_key(segments[0]) not in _ALLOWED_PATCH_ROOTS:
                raise PlanPatchError(f"JSON patch path '{path}' is outside the repair allowlist.")
            if _normalize_key(segments[0]) == "request":
                if len(segments) != 2 or _normalize_key(segments[1]) not in {
                    "wait_until",
                    "wait_for_selector",
                    "timeout_ms",
                }:
                    raise PlanPatchError(
                        f"JSON patch path '{path}' is outside the request wait-condition allowlist."
                    )
            for segment in segments:
                normalized = _normalize_key(segment)
                if normalized in _FORBIDDEN_PATCH_KEYS or _is_sensitive_key(normalized):
                    raise PlanPatchError(f"JSON patch path '{path}' contains a forbidden setting.")
            if "from" in operation:
                raise PlanPatchError("JSON patch 'from' paths are not allowed.")
            _walk_patch_keys(operation.get("value"), tuple(segments))
            if "value" in operation:
                if len(segments) == 1:
                    _validate_wait_value(_normalize_key(segments[0]), operation["value"])
                elif _normalize_key(segments[0]) == "request":
                    _validate_wait_value(_normalize_key(segments[1]), operation["value"])
            result.append(copy.deepcopy(dict(operation)))
        return result

    raise PlanPatchError("A repair patch must be an object or a JSON patch operation list.")


validate_repair_plan_patch = validate_plan_patch


def apply_plan_patch(plan: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a validated mapping patch without mutating the source plan."""

    validated = validate_plan_patch(patch)
    if not isinstance(validated, Mapping):
        raise PlanPatchError("apply_plan_patch accepts an object patch, not JSON patch operations.")
    result = copy.deepcopy(dict(plan))
    for key, value in validated.items():
        if _normalize_key(key) == "request":
            current_request = result.get(key, {})
            merged_request = dict(current_request) if isinstance(current_request, Mapping) else {}
            merged_request.update(copy.deepcopy(dict(value)))
            result[key] = merged_request
        else:
            result[key] = copy.deepcopy(value)
    return result


class _HTMLStructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: Counter[str] = Counter()
        self.attributes: Counter[str] = Counter()
        self.max_depth = 0
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags[tag.lower()] += 1
        for name, _value in attrs:
            self.attributes[name.lower()] += 1
        self._depth += 1
        self.max_depth = max(self.max_depth, self._depth)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags[tag.lower()] += 1
        for name, _value in attrs:
            self.attributes[name.lower()] += 1

    def handle_endtag(self, _tag: str) -> None:
        self._depth = max(0, self._depth - 1)


def _json_shape(value: Any, depth: int = 0) -> Any:
    if depth >= 6:
        return {"type": type(value).__name__, "truncated": True}
    if isinstance(value, Mapping):
        return {str(key): _json_shape(item, depth + 1) for key, item in value.items() if not _is_sensitive_key(key)}
    if isinstance(value, list):
        samples = [_json_shape(item, depth + 1) for item in value[:5]]
        return {"type": "array", "length": len(value), "samples": samples}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    return type(value).__name__


def _structural_summary(data: bytes, media_type: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "storage_policy": "structure_only",
        "media_type": media_type,
        "size_bytes": len(data),
        "source_sha256": hashlib.sha256(data).hexdigest(),
    }
    lowered = media_type.lower()
    text: str | None = None
    if lowered.startswith("text/") or "json" in lowered or "xml" in lowered or "html" in lowered:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
    if text is not None:
        summary["text"] = {
            "characters": len(text),
            "lines": len(text.splitlines()),
        }
    if text is not None and "html" in lowered:
        parser = _HTMLStructureParser()
        try:
            parser.feed(text)
            summary["html"] = {
                "tags": dict(sorted(parser.tags.items())),
                "attribute_names": dict(sorted(parser.attributes.items())),
                "max_depth": parser.max_depth,
            }
        except Exception:
            summary["html"] = {"parse_error": True}
    elif text is not None and "json" in lowered:
        try:
            summary["json_shape"] = _json_shape(json.loads(text))
        except (json.JSONDecodeError, UnicodeDecodeError):
            summary["json_shape"] = {"parse_error": True}
    return summary


def _episode_id(value: str | RepairEpisode) -> str:
    if isinstance(value, RepairEpisode):
        return value.id
    return str(value)


def _proposal_id(value: str | RepairProposal | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, RepairProposal):
        return value.id
    return str(value)


class ExperienceStore:
    """Explicit local SQLite store for RepairEpisode v1 records."""

    def __init__(self, path: str | Path):
        if path is None or not str(path).strip():
            raise ValueError("ExperienceStore requires an explicit SQLite path.")
        if str(path) == ":memory:":
            raise ValueError("ExperienceStore requires a filesystem path so its adjacent CAS is durable.")
        resolved = Path(path).expanduser().resolve()
        if resolved.exists() and resolved.is_dir():
            raise ValueError(f"ExperienceStore path is a directory: {resolved}")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self.path = resolved
        self.cas_root = resolved.with_name(f"{resolved.name}.cas")
        if self.cas_root.exists() and not self.cas_root.is_dir():
            raise ValueError(f"ExperienceStore CAS path is not a directory: {self.cas_root}")
        self.objects_root = self.cas_root / "objects"
        self.objects_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    def __enter__(self) -> "ExperienceStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
                self._connection = None  # type: ignore[assignment]

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise ExperienceStoreError("ExperienceStore is closed.")
        return self._connection

    def _initialize_schema(self) -> None:
        with self._lock, self._require_connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS experience_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cas_objects (
                    sha256 TEXT PRIMARY KEY,
                    source_sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    storage_mode TEXT NOT NULL,
                    object_path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS repair_episodes (
                    id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    authorization_category TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    page_pattern TEXT NOT NULL,
                    retain_full_content INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS repair_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id TEXT NOT NULL REFERENCES repair_episodes(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    artifact_sha256 TEXT REFERENCES cas_objects(sha256),
                    UNIQUE (episode_id, sequence)
                );

                CREATE TABLE IF NOT EXISTS repair_proposals (
                    id TEXT PRIMARY KEY,
                    episode_id TEXT NOT NULL REFERENCES repair_episodes(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    patch_json TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    status TEXT NOT NULL,
                    historical INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS repair_validations (
                    id TEXT PRIMARY KEY,
                    episode_id TEXT NOT NULL REFERENCES repair_episodes(id) ON DELETE CASCADE,
                    proposal_id TEXT REFERENCES repair_proposals(id),
                    created_at TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    validator TEXT NOT NULL,
                    checks_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    evidence_sha256 TEXT REFERENCES cas_objects(sha256)
                );

                CREATE TABLE IF NOT EXISTS repair_decisions (
                    id TEXT PRIMARY KEY,
                    episode_id TEXT NOT NULL REFERENCES repair_episodes(id) ON DELETE CASCADE,
                    proposal_id TEXT REFERENCES repair_proposals(id),
                    created_at TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS legacy_imports (
                    source_sha256 TEXT NOT NULL,
                    line_number INTEGER NOT NULL,
                    episode_id TEXT NOT NULL REFERENCES repair_episodes(id) ON DELETE CASCADE,
                    PRIMARY KEY (source_sha256, line_number)
                );

                CREATE INDEX IF NOT EXISTS idx_repair_events_episode ON repair_events(episode_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_repair_proposals_episode ON repair_proposals(episode_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_repair_validations_episode ON repair_validations(episode_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_repair_decisions_episode ON repair_decisions(episode_id, created_at);
                """
            )
            existing = connection.execute(
                "SELECT value FROM experience_meta WHERE key = 'schema_version'"
            ).fetchone()
            if existing is not None and int(existing["value"]) != SCHEMA_VERSION:
                raise ExperienceStoreError(
                    f"Unsupported experience-store schema {existing['value']}; expected {SCHEMA_VERSION}."
                )
            connection.execute(
                "INSERT OR IGNORE INTO experience_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            episode_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(repair_episodes)")
            }
            if "retain_full_content" not in episode_columns:
                connection.execute(
                    "ALTER TABLE repair_episodes "
                    "ADD COLUMN retain_full_content INTEGER NOT NULL DEFAULT 0"
                )

    @property
    def schema_version(self) -> int:
        return SCHEMA_VERSION

    def create_episode(
        self,
        *,
        authorization_category: str | AuthorizationCategory,
        source_url: str = "",
        page_pattern: str = "",
        retain_full_content: bool = False,
        metadata: Mapping[str, Any] | None = None,
        episode_id: str | None = None,
        status: str = "open",
        created_at: str | None = None,
    ) -> RepairEpisode:
        category = (
            authorization_category.value
            if isinstance(authorization_category, AuthorizationCategory)
            else str(authorization_category).strip().lower().replace("-", "_").replace(" ", "_")
        )
        if not category:
            raise ValueError("authorization_category is required.")
        if not isinstance(retain_full_content, bool):
            raise ValueError("retain_full_content must be true or false.")
        if retain_full_content and category not in {
            AuthorizationCategory.SYNTHETIC_LOCAL.value,
            AuthorizationCategory.AUTHORIZED.value,
        }:
            raise ValueError(
                "Full episode content may be retained only for synthetic_local "
                "or explicitly authorized sources."
            )
        identifier = episode_id or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identifier):
            raise ValueError("episode_id contains unsupported characters.")
        timestamp = created_at or _utc_now()
        safe_metadata = sanitize_episode_payload(metadata or {}, category)
        safe_source_url = _sanitize_url(source_url)
        with self._lock, self._require_connection() as connection:
            connection.execute(
                """
                INSERT INTO repair_episodes(
                    id, schema_version, created_at, updated_at,
                    authorization_category, source_url, page_pattern,
                    retain_full_content, status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    SCHEMA_VERSION,
                    timestamp,
                    timestamp,
                    category,
                    safe_source_url,
                    _sanitize_url(page_pattern),
                    int(retain_full_content),
                    status,
                    _json_dump(safe_metadata),
                ),
            )
        return self.get_episode_record(identifier)

    start_episode = create_episode

    def get_episode_record(self, episode_id: str | RepairEpisode) -> RepairEpisode:
        identifier = _episode_id(episode_id)
        with self._lock:
            row = self._require_connection().execute(
                "SELECT * FROM repair_episodes WHERE id = ?", (identifier,)
            ).fetchone()
        if row is None:
            raise EpisodeNotFoundError(f"Unknown repair episode: {identifier}")
        return _episode_from_row(row)

    def list_episodes(
        self,
        *,
        limit: int = 100,
        status: str | None = None,
    ) -> list[RepairEpisode]:
        if limit < 1 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000.")
        query = "SELECT * FROM repair_episodes"
        params: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._require_connection().execute(query, params).fetchall()
        return [_episode_from_row(row) for row in rows]

    def _append_event_in_transaction(
        self,
        connection: sqlite3.Connection,
        identifier: str,
        event_type: str,
        payload: Any,
        artifact: str | None,
        timestamp: str,
    ) -> RepairEvent:
        if not event_type.strip():
            raise ValueError("event_type is required.")
        episode_row = self._ensure_episode(connection, identifier)
        safe_payload = sanitize_episode_payload(
            {} if payload is None else payload,
            str(episode_row["authorization_category"]),
        )
        if artifact is not None:
            self._ensure_cas_object(connection, artifact)
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS value FROM repair_events WHERE episode_id = ?",
                (identifier,),
            ).fetchone()["value"]
        )
        cursor = connection.execute(
            """
            INSERT INTO repair_events(
                episode_id, sequence, created_at, event_type, payload_json, artifact_sha256
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (identifier, sequence, timestamp, event_type, _json_dump(safe_payload), artifact),
        )
        connection.execute(
            "UPDATE repair_episodes SET updated_at = ? WHERE id = ?", (timestamp, identifier)
        )
        event_id = int(cursor.lastrowid)
        return RepairEvent(event_id, identifier, sequence, timestamp, event_type, safe_payload, artifact)

    def append_event(
        self,
        episode_id: str | RepairEpisode,
        event_type: str,
        payload: Any = None,
        *,
        artifact_sha256: str | CASObject | None = None,
        created_at: str | None = None,
    ) -> RepairEvent:
        identifier = _episode_id(episode_id)
        artifact = artifact_sha256.sha256 if isinstance(artifact_sha256, CASObject) else artifact_sha256
        timestamp = created_at or _utc_now()
        with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                event = self._append_event_in_transaction(
                    connection,
                    identifier,
                    event_type,
                    payload,
                    artifact,
                    timestamp,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return event

    record_event = append_event

    def put_capture(
        self,
        episode_id: str | RepairEpisode,
        content: str | bytes,
        *,
        media_type: str = "text/html",
        authorization_category: str | AuthorizationCategory | None = None,
    ) -> CASObject:
        identifier = _episode_id(episode_id)
        episode = self.get_episode_record(identifier)
        category = authorization_category or episode.authorization_category
        normalized = (
            category.value if isinstance(category, AuthorizationCategory) else str(category).lower().replace("-", "_").replace(" ", "_")
        )
        if _is_synthetic_local(normalized) and not _is_synthetic_local(episode.authorization_category):
            raise ExperienceStoreError(
                "Capture authorization cannot be escalated beyond the episode category."
            )
        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        source_sha = hashlib.sha256(raw).hexdigest()
        synthetic_local = _is_synthetic_local(normalized)
        explicitly_authorized_full = bool(
            episode.retain_full_content
            and episode.authorization_category == AuthorizationCategory.AUTHORIZED.value
            and normalized == AuthorizationCategory.AUTHORIZED.value
        )
        textual_capture = (
            media_type.lower().startswith("text/")
            or "json" in media_type.lower()
            or "html" in media_type.lower()
            or "xml" in media_type.lower()
        )

        storage_mode = (
            "full"
            if synthetic_local
            else "redacted_full_opt_in"
            if explicitly_authorized_full and textual_capture
            else "structure_only"
        )
        stored = raw
        if (synthetic_local or explicitly_authorized_full) and textual_capture:
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                decoded = None
                stored = _json_dump(_structural_summary(raw, media_type)).encode("utf-8")
                storage_mode = "structure_only_invalid_utf8"
            if decoded is not None:
                changed = False
                if "json" in media_type.lower():
                    try:
                        parsed_json = json.loads(decoded)
                    except (json.JSONDecodeError, TypeError):
                        redacted, changed = _redact_text(decoded)
                        stored = redacted.encode("utf-8")
                    else:
                        sanitized_json = sanitize_payload(parsed_json)
                        changed = sanitized_json != parsed_json
                        stored = _json_dump(sanitized_json).encode("utf-8")
                else:
                    redacted, changed = _redact_text(decoded)
                    stored = redacted.encode("utf-8")
                if changed:
                    storage_mode = (
                        "redacted_full"
                        if synthetic_local
                        else "redacted_full_opt_in"
                    )
        elif not synthetic_local:
            stored = _json_dump(_structural_summary(raw, media_type)).encode("utf-8")

        digest = hashlib.sha256(stored).hexdigest()
        object_path = self._cas_relative_path(digest)
        self._write_cas_object(digest, object_path, stored)
        timestamp = _utc_now()
        with self._lock, self._require_connection() as connection:
            self._ensure_episode(connection, identifier)
            connection.execute(
                """
                INSERT OR IGNORE INTO cas_objects(
                    sha256, source_sha256, size_bytes, media_type, storage_mode, object_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (digest, source_sha, len(stored), media_type, storage_mode, object_path, timestamp),
            )
            row = connection.execute("SELECT * FROM cas_objects WHERE sha256 = ?", (digest,)).fetchone()
            if row is None or str(row["object_path"]) != object_path:
                raise ExperienceStoreError(f"CAS integrity failure for {digest}.")
        return _cas_from_row(row)

    store_capture = put_capture

    def add_capture(
        self,
        episode_id: str | RepairEpisode,
        capture_type: str,
        content: str | bytes,
        *,
        media_type: str = "text/html",
        metadata: Mapping[str, Any] | None = None,
        authorization_category: str | AuthorizationCategory | None = None,
    ) -> RepairEvent:
        artifact = self.put_capture(
            episode_id,
            content,
            media_type=media_type,
            authorization_category=authorization_category,
        )
        payload = {"capture_type": capture_type, "storage_mode": artifact.storage_mode}
        if metadata:
            payload["metadata"] = sanitize_payload(metadata)
        return self.append_event(episode_id, "capture", payload, artifact_sha256=artifact)

    def get_blob(self, sha256: str) -> bytes:
        with self._lock:
            row = self._require_connection().execute(
                "SELECT * FROM cas_objects WHERE sha256 = ?", (sha256,)
            ).fetchone()
        if row is None:
            raise ExperienceStoreError(f"Unknown CAS object: {sha256}")
        path = self._resolve_cas_path(str(row["object_path"]), expected_sha256=sha256)
        try:
            body = path.read_bytes()
        except OSError as exc:
            raise ExperienceStoreError(f"CAS object is unreadable: {sha256}") from exc
        if len(body) != int(row["size_bytes"]) or hashlib.sha256(body).hexdigest() != sha256:
            raise ExperienceStoreError(f"CAS object failed SHA-256 verification: {sha256}")
        return body

    def get_cas_object(self, sha256: str) -> CASObject:
        with self._lock:
            row = self._require_connection().execute(
                "SELECT * FROM cas_objects WHERE sha256 = ?", (sha256,)
            ).fetchone()
        if row is None:
            raise ExperienceStoreError(f"Unknown CAS object: {sha256}")
        return _cas_from_row(row)

    def add_proposal(
        self,
        episode_id: str | RepairEpisode,
        patch: Any,
        *,
        rationale: str = "",
        source: str = "local",
        proposal_id: str | None = None,
        historical: bool = False,
        created_at: str | None = None,
    ) -> RepairProposal:
        identifier = _episode_id(episode_id)
        validated_patch = sanitize_payload(validate_plan_patch(patch))
        proposal_identifier = proposal_id or uuid.uuid4().hex
        timestamp = created_at or _utc_now()
        status = "historical_candidate" if historical else "candidate"
        safe_source = _redact_text(source)[0]
        safe_rationale = _redact_text(rationale)[0]
        with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure_episode(connection, identifier)
                connection.execute(
                    """
                    INSERT INTO repair_proposals(
                        id, episode_id, created_at, source, patch_json, rationale, status, historical
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proposal_identifier,
                        identifier,
                        timestamp,
                        safe_source,
                        _json_dump(validated_patch),
                        safe_rationale,
                        status,
                        int(historical),
                    ),
                )
                self._append_event_in_transaction(
                    connection,
                    identifier,
                    "proposal",
                    {
                        "proposal_id": proposal_identifier,
                        "status": status,
                        "source": safe_source,
                    },
                    None,
                    timestamp,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        proposal = RepairProposal(
            proposal_identifier,
            identifier,
            timestamp,
            safe_source,
            validated_patch,
            safe_rationale,
            status,
            historical,
        )
        return proposal

    record_proposal = add_proposal

    def add_validation(
        self,
        episode_id: str | RepairEpisode,
        *,
        passed: bool,
        proposal_id: str | RepairProposal | None = None,
        validator: str = "local",
        checks: Any = None,
        metrics: Any = None,
        evidence_sha256: str | CASObject | None = None,
        validation_id: str | None = None,
        created_at: str | None = None,
    ) -> RepairValidation:
        if not isinstance(passed, bool):
            raise ValueError("passed must be true or false.")
        identifier = _episode_id(episode_id)
        proposal_identifier = _proposal_id(proposal_id)
        evidence = evidence_sha256.sha256 if isinstance(evidence_sha256, CASObject) else evidence_sha256
        result_identifier = validation_id or uuid.uuid4().hex
        timestamp = created_at or _utc_now()
        safe_validator = _redact_text(validator)[0]
        with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                episode_row = self._ensure_episode(connection, identifier)
                category = str(episode_row["authorization_category"])
                safe_checks = sanitize_episode_payload({} if checks is None else checks, category)
                safe_metrics = sanitize_episode_payload({} if metrics is None else metrics, category)
                if proposal_identifier:
                    self._ensure_proposal(connection, identifier, proposal_identifier)
                if evidence:
                    self._ensure_cas_object(connection, evidence)
                connection.execute(
                    """
                    INSERT INTO repair_validations(
                        id, episode_id, proposal_id, created_at, passed, validator,
                        checks_json, metrics_json, evidence_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result_identifier,
                        identifier,
                        proposal_identifier,
                        timestamp,
                        int(passed),
                        safe_validator,
                        _json_dump(safe_checks),
                        _json_dump(safe_metrics),
                        evidence,
                    ),
                )
                self._append_event_in_transaction(
                    connection,
                    identifier,
                    "validation",
                    {
                        "validation_id": result_identifier,
                        "proposal_id": proposal_identifier,
                        "passed": passed,
                    },
                    evidence,
                    timestamp,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        result = RepairValidation(
            result_identifier,
            identifier,
            proposal_identifier,
            timestamp,
            passed,
            safe_validator,
            safe_checks,
            safe_metrics,
            evidence,
        )
        return result

    record_validation = add_validation

    def add_decision(
        self,
        episode_id: str | RepairEpisode,
        outcome: str,
        *,
        proposal_id: str | RepairProposal | None = None,
        actor: str = "human",
        reason: str = "",
        metadata: Any = None,
        decision_id: str | None = None,
        created_at: str | None = None,
    ) -> RepairDecision:
        identifier = _episode_id(episode_id)
        proposal_identifier = _proposal_id(proposal_id)
        normalized_outcome = _normalize_key(outcome)
        allowed_outcomes = {
            "accepted",
            "rejected",
            "deferred",
            "superseded",
            "historical_candidate",
            "needs_review",
        }
        if normalized_outcome not in allowed_outcomes:
            raise ValueError(f"Unsupported repair decision: {outcome}")
        result_identifier = decision_id or uuid.uuid4().hex
        timestamp = created_at or _utc_now()
        safe_actor = _redact_text(actor)[0]
        safe_reason = _redact_text(reason)[0]
        with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                episode_row = self._ensure_episode(connection, identifier)
                safe_metadata = sanitize_episode_payload(
                    {} if metadata is None else metadata,
                    str(episode_row["authorization_category"]),
                )
                proposal_row = None
                if proposal_identifier:
                    proposal_row = self._ensure_proposal(connection, identifier, proposal_identifier)
                    if bool(proposal_row["historical"]) and normalized_outcome == "accepted":
                        raise ExperienceStoreError(
                            "Historical RepairPersistence candidates cannot be promoted; re-propose and validate them first."
                        )
                if normalized_outcome == "accepted":
                    if proposal_row is None or proposal_identifier is None:
                        raise ExperienceStoreError(
                            "An accepted decision requires a concrete proposal."
                        )
                    validation_row = connection.execute(
                        """
                        SELECT passed FROM repair_validations
                        WHERE episode_id = ? AND proposal_id = ?
                        ORDER BY created_at DESC, rowid DESC
                        LIMIT 1
                        """,
                        (identifier, proposal_identifier),
                    ).fetchone()
                    if validation_row is None or not bool(validation_row["passed"]):
                        raise ExperienceStoreError(
                            "An accepted decision requires the proposal's latest replay validation to pass."
                        )
                connection.execute(
                    """
                    INSERT INTO repair_decisions(
                        id, episode_id, proposal_id, created_at, outcome, actor, reason, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result_identifier,
                        identifier,
                        proposal_identifier,
                        timestamp,
                        normalized_outcome,
                        safe_actor,
                        safe_reason,
                        _json_dump(safe_metadata),
                    ),
                )
                if proposal_row is not None:
                    connection.execute(
                        "UPDATE repair_proposals SET status = ? WHERE id = ?",
                        (normalized_outcome, proposal_identifier),
                    )
                if normalized_outcome in {"accepted", "rejected", "superseded"}:
                    episode_status = "decided"
                elif normalized_outcome == "historical_candidate":
                    episode_status = str(episode_row["status"])
                else:
                    episode_status = "open"
                connection.execute(
                    "UPDATE repair_episodes SET updated_at = ?, status = ? WHERE id = ?",
                    (timestamp, episode_status, identifier),
                )
                self._append_event_in_transaction(
                    connection,
                    identifier,
                    "decision",
                    {
                        "decision_id": result_identifier,
                        "proposal_id": proposal_identifier,
                        "outcome": normalized_outcome,
                    },
                    None,
                    timestamp,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        result = RepairDecision(
            result_identifier,
            identifier,
            proposal_identifier,
            timestamp,
            normalized_outcome,
            safe_actor,
            safe_reason,
            safe_metadata,
        )
        return result

    record_decision = add_decision

    def get_episode(
        self,
        episode_id: str | RepairEpisode,
        *,
        include_artifacts: bool = False,
    ) -> dict[str, Any]:
        episode = self.get_episode_record(episode_id)
        identifier = episode.id
        with self._lock:
            connection = self._require_connection()
            event_rows = connection.execute(
                "SELECT * FROM repair_events WHERE episode_id = ? ORDER BY sequence", (identifier,)
            ).fetchall()
            proposal_rows = connection.execute(
                "SELECT * FROM repair_proposals WHERE episode_id = ? ORDER BY created_at, id", (identifier,)
            ).fetchall()
            validation_rows = connection.execute(
                "SELECT * FROM repair_validations WHERE episode_id = ? ORDER BY created_at, id", (identifier,)
            ).fetchall()
            decision_rows = connection.execute(
                "SELECT * FROM repair_decisions WHERE episode_id = ? ORDER BY created_at, id", (identifier,)
            ).fetchall()

        events = [_event_from_row(row).to_dict() for row in event_rows]
        proposals = [_proposal_from_row(row).to_dict() for row in proposal_rows]
        validations = [_validation_from_row(row).to_dict() for row in validation_rows]
        decisions = [_decision_from_row(row).to_dict() for row in decision_rows]
        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "episode": episode.to_dict(),
            "events": events,
            "proposals": proposals,
            "validations": validations,
            "decisions": decisions,
            # Singular aliases make the v1 episode/proposal/validation/decision
            # envelope convenient for consumers that expect one current item.
            "proposal": proposals[-1] if proposals else None,
            "validation": validations[-1] if validations else None,
            "decision": decisions[-1] if decisions else None,
        }
        artifact_hashes = sorted(
            {
                value
                for value in [
                    *(event.get("artifact_sha256") for event in events),
                    *(validation.get("evidence_sha256") for validation in validations),
                ]
                if value
            }
        )
        result["artifacts"] = []
        for digest in artifact_hashes:
            metadata = self.get_cas_object(digest).to_dict()
            if include_artifacts:
                metadata["encoding"] = "base64"
                metadata["body"] = base64.b64encode(self.get_blob(digest)).decode("ascii")
            result["artifacts"].append(metadata)
        return result

    show_episode = get_episode

    def export_episode(
        self,
        episode_id: str | RepairEpisode,
        destination: str | Path | None = None,
        *,
        include_artifacts: bool = False,
    ) -> dict[str, Any] | Path:
        payload = self.get_episode(episode_id, include_artifacts=include_artifacts)
        if destination is None:
            return payload
        output = Path(destination).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return output

    def import_legacy_jsonl(
        self,
        source: str | Path,
        *,
        authorization_category: str | AuthorizationCategory = AuthorizationCategory.UNKNOWN,
    ) -> LegacyImportResult:
        """Import RepairPersistence JSONL as non-promotable historical candidates."""

        path = Path(source).expanduser()
        raw = path.read_bytes()
        source_sha = hashlib.sha256(raw).hexdigest()
        imported = 0
        skipped = 0
        invalid = 0
        episode_ids: list[str] = []
        for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), start=1):
            if not line.strip():
                continue
            with self._lock:
                existing = self._require_connection().execute(
                    "SELECT episode_id FROM legacy_imports WHERE source_sha256 = ? AND line_number = ?",
                    (source_sha, line_number),
                ).fetchone()
            if existing is not None:
                skipped += 1
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if not isinstance(entry, Mapping):
                invalid += 1
                continue
            field_name = str(entry.get("field", "")).strip()
            old_selector = str(entry.get("old", "")).strip()
            new_selector = str(entry.get("new", "")).strip()
            if not field_name or not new_selector:
                invalid += 1
                continue
            # RepairPersistence files may contain failed observations or old
            # rows without an explicit validation result. Only a literal JSON
            # ``true`` is eligible to become a historical candidate.
            if entry.get("ok") is not True:
                invalid += 1
                continue

            episode = self.create_episode(
                authorization_category=authorization_category,
                source_url=str(entry.get("page_url", "")),
                page_pattern=str(entry.get("page_pattern", "")),
                metadata={
                    "origin": "RepairPersistence JSONL",
                    "legacy_source_sha256": source_sha,
                    "legacy_line_number": line_number,
                },
                status="historical",
                created_at=str(entry.get("at") or _utc_now()),
            )
            self.append_event(
                episode,
                "legacy_import",
                {"source": "RepairPersistence", "line_number": line_number},
                created_at=str(entry.get("at") or _utc_now()),
            )
            proposal = self.add_proposal(
                episode,
                {
                    "fields": [
                        {
                            "name": field_name,
                            "selector": new_selector,
                            "previous_selector": old_selector,
                        }
                    ]
                },
                source="legacy_repair_persistence",
                rationale="Imported historical selector candidate; requires a fresh proposal and validation before use.",
                historical=True,
                created_at=str(entry.get("at") or _utc_now()),
            )
            self.add_validation(
                episode,
                proposal_id=proposal,
                passed=True,
                validator="legacy_repair_persistence",
                checks={"historical_result_only": True},
                created_at=str(entry.get("at") or _utc_now()),
            )
            self.add_decision(
                episode,
                "historical_candidate",
                proposal_id=proposal,
                actor="importer",
                reason="Legacy observations are candidates only and are never promoted automatically.",
                created_at=str(entry.get("at") or _utc_now()),
            )
            with self._lock, self._require_connection() as connection:
                connection.execute(
                    "INSERT INTO legacy_imports(source_sha256, line_number, episode_id) VALUES (?, ?, ?)",
                    (source_sha, line_number, episode.id),
                )
            imported += 1
            episode_ids.append(episode.id)
        return LegacyImportResult(imported, skipped, invalid, tuple(episode_ids))

    import_repair_persistence = import_legacy_jsonl

    @staticmethod
    def _cas_relative_path(sha256: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ExperienceStoreError("CAS identifiers must be lowercase SHA-256 hex digests.")
        return PurePosixPath("objects", sha256[:2], sha256[2:4], sha256).as_posix()

    def _resolve_cas_path(self, relative_path: str, *, expected_sha256: str) -> Path:
        expected = self._cas_relative_path(expected_sha256)
        if relative_path != expected:
            raise ExperienceStoreError(
                f"CAS index path does not match its SHA-256 identifier: {expected_sha256}"
            )
        if "\\" in relative_path or ":" in relative_path:
            raise ExperienceStoreError("CAS index contains an unsafe object path.")
        pure = PurePosixPath(relative_path)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise ExperienceStoreError("CAS index contains an unsafe object path.")
        root = self.cas_root.resolve()
        candidate = (root / Path(*pure.parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ExperienceStoreError("CAS object path escapes the configured store.") from exc
        return candidate

    def _write_cas_object(self, sha256: str, relative_path: str, body: bytes) -> None:
        if hashlib.sha256(body).hexdigest() != sha256:
            raise ExperienceStoreError(f"Refusing to write a CAS object with the wrong digest: {sha256}")
        destination = self._resolve_cas_path(relative_path, expected_sha256=sha256)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            try:
                existing = destination.read_bytes()
            except OSError as exc:
                raise ExperienceStoreError(f"Existing CAS object is unreadable: {sha256}") from exc
            if existing != body:
                raise ExperienceStoreError(f"Existing CAS object failed integrity verification: {sha256}")
            return

        temporary = destination.parent / f".{sha256}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            # The temporary file is in the destination directory, so replace is
            # atomic on the target filesystem.  Re-check a concurrently created
            # object before replacing it.
            if destination.exists():
                existing = destination.read_bytes()
                if existing != body:
                    raise ExperienceStoreError(
                        f"Concurrent CAS object failed integrity verification: {sha256}"
                    )
            else:
                os.replace(temporary, destination)
        except OSError as exc:
            raise ExperienceStoreError(f"Could not persist CAS object: {sha256}") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

        persisted = destination.read_bytes()
        if persisted != body or hashlib.sha256(persisted).hexdigest() != sha256:
            raise ExperienceStoreError(f"CAS object failed post-write verification: {sha256}")

    @staticmethod
    def _ensure_episode(connection: sqlite3.Connection, episode_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM repair_episodes WHERE id = ?", (episode_id,)).fetchone()
        if row is None:
            raise EpisodeNotFoundError(f"Unknown repair episode: {episode_id}")
        return row

    @staticmethod
    def _ensure_proposal(
        connection: sqlite3.Connection, episode_id: str, proposal_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM repair_proposals WHERE id = ? AND episode_id = ?",
            (proposal_id, episode_id),
        ).fetchone()
        if row is None:
            raise ExperienceStoreError(
                f"Unknown proposal {proposal_id} for repair episode {episode_id}."
            )
        return row

    @staticmethod
    def _ensure_cas_object(connection: sqlite3.Connection, sha256: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM cas_objects WHERE sha256 = ?", (sha256,)).fetchone()
        if row is None:
            raise ExperienceStoreError(f"Unknown CAS object: {sha256}")
        return row


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: str) -> Any:
    return json.loads(value)


def _episode_from_row(row: sqlite3.Row) -> RepairEpisode:
    return RepairEpisode(
        id=str(row["id"]),
        schema_version=int(row["schema_version"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        authorization_category=str(row["authorization_category"]),
        source_url=str(row["source_url"]),
        page_pattern=str(row["page_pattern"]),
        retain_full_content=bool(row["retain_full_content"]),
        status=str(row["status"]),
        metadata=_json_load(row["metadata_json"]),
    )


def _event_from_row(row: sqlite3.Row) -> RepairEvent:
    return RepairEvent(
        id=int(row["id"]),
        episode_id=str(row["episode_id"]),
        sequence=int(row["sequence"]),
        created_at=str(row["created_at"]),
        event_type=str(row["event_type"]),
        payload=_json_load(row["payload_json"]),
        artifact_sha256=row["artifact_sha256"],
    )


def _proposal_from_row(row: sqlite3.Row) -> RepairProposal:
    return RepairProposal(
        id=str(row["id"]),
        episode_id=str(row["episode_id"]),
        created_at=str(row["created_at"]),
        source=str(row["source"]),
        patch=_json_load(row["patch_json"]),
        rationale=str(row["rationale"]),
        status=str(row["status"]),
        historical=bool(row["historical"]),
    )


def _validation_from_row(row: sqlite3.Row) -> RepairValidation:
    return RepairValidation(
        id=str(row["id"]),
        episode_id=str(row["episode_id"]),
        proposal_id=row["proposal_id"],
        created_at=str(row["created_at"]),
        passed=bool(row["passed"]),
        validator=str(row["validator"]),
        checks=_json_load(row["checks_json"]),
        metrics=_json_load(row["metrics_json"]),
        evidence_sha256=row["evidence_sha256"],
    )


def _decision_from_row(row: sqlite3.Row) -> RepairDecision:
    return RepairDecision(
        id=str(row["id"]),
        episode_id=str(row["episode_id"]),
        proposal_id=row["proposal_id"],
        created_at=str(row["created_at"]),
        outcome=str(row["outcome"]),
        actor=str(row["actor"]),
        reason=str(row["reason"]),
        metadata=_json_load(row["metadata_json"]),
    )


def _cas_from_row(row: sqlite3.Row) -> CASObject:
    return CASObject(
        sha256=str(row["sha256"]),
        source_sha256=str(row["source_sha256"]),
        size_bytes=int(row["size_bytes"]),
        media_type=str(row["media_type"]),
        storage_mode=str(row["storage_mode"]),
        object_path=str(row["object_path"]),
        created_at=str(row["created_at"]),
    )


__all__ = [
    "AuthorizationCategory",
    "CASObject",
    "EpisodeNotFoundError",
    "ExperienceStore",
    "ExperienceStoreError",
    "LegacyImportResult",
    "PlanPatchError",
    "RepairDecision",
    "RepairEpisode",
    "RepairEvent",
    "RepairProposal",
    "RepairValidation",
    "SCHEMA_VERSION",
    "apply_plan_patch",
    "sanitize_episode_payload",
    "sanitize_payload",
    "sanitize_source_url",
    "validate_plan_patch",
    "validate_repair_plan_patch",
]
