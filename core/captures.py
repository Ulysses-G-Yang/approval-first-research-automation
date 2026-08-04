"""Bounded, passive JSON capture support for ``GenericSpider``.

Only two v2.1 capture types are supported:

* ``embedded_json`` reads a JSON script/element selected from the loaded page.
* ``network_json`` passively records the first matching successful GET
  XHR/fetch JSON response.  It never initiates a request.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


DEFAULT_MAX_BYTES = 1_000_000
MAX_CAPTURE_BYTES = 16_000_000


class CaptureConfigurationError(ValueError):
    """Raised when a capture plan is malformed."""


class RequiredCaptureError(RuntimeError):
    """Raised when one or more required captures cannot be produced."""


@dataclass(frozen=True)
class CaptureSpec:
    name: str
    type: str
    required: bool
    max_bytes: int
    selector: str = ""
    url_glob: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], index: int) -> "CaptureSpec":
        if not isinstance(value, Mapping):
            raise CaptureConfigurationError(f"captures[{index}] must be an object.")
        name = str(value.get("name", "")).strip()
        capture_type = str(value.get("type", "")).strip()
        if not name:
            raise CaptureConfigurationError(f"captures[{index}].name is required.")
        if name == "page_url":
            raise CaptureConfigurationError(
                f"captures[{index}].name cannot use the reserved page_url context key."
            )
        if "." in name:
            raise CaptureConfigurationError(
                f"captures[{index}].name cannot contain '.' because source paths use dots."
            )
        if capture_type not in {"embedded_json", "network_json"}:
            raise CaptureConfigurationError(
                f"captures[{index}].type must be embedded_json or network_json."
            )
        if "required" not in value:
            raise CaptureConfigurationError(f"captures[{index}].required is required.")
        raw_required = value.get("required")
        if not isinstance(raw_required, bool):
            raise CaptureConfigurationError(f"captures[{index}].required must be true or false.")
        if "max_bytes" not in value:
            raise CaptureConfigurationError(f"captures[{index}].max_bytes is required.")
        raw_max_bytes = value.get("max_bytes")
        if isinstance(raw_max_bytes, bool) or not isinstance(raw_max_bytes, int):
            raise CaptureConfigurationError(f"captures[{index}].max_bytes must be an integer.")
        max_bytes = raw_max_bytes
        if not 1 <= max_bytes <= MAX_CAPTURE_BYTES:
            raise CaptureConfigurationError(
                f"captures[{index}].max_bytes must be between 1 and {MAX_CAPTURE_BYTES}."
            )
        selector = str(value.get("selector", "")).strip()
        url_glob = str(value.get("url_glob", "")).strip()
        if capture_type == "embedded_json" and not selector:
            raise CaptureConfigurationError(f"captures[{index}].selector is required.")
        if capture_type == "network_json" and not url_glob:
            raise CaptureConfigurationError(f"captures[{index}].url_glob is required.")
        return cls(
            name=name,
            type=capture_type,
            required=raw_required,
            max_bytes=max_bytes,
            selector=selector,
            url_glob=url_glob,
        )


def parse_capture_specs(values: Any) -> tuple[CaptureSpec, ...]:
    if values in (None, []):
        return ()
    if not isinstance(values, list):
        raise CaptureConfigurationError("captures must be a list.")
    specs = tuple(CaptureSpec.from_mapping(value, index) for index, value in enumerate(values))
    names = [spec.name for spec in specs]
    if len(names) != len(set(names)):
        raise CaptureConfigurationError("capture names must be unique.")
    return specs


def _property(value: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(value, name)
    except Exception:
        return default


class PageCaptureSession:
    """Collect one page's declared captures without initiating network traffic."""

    def __init__(
        self,
        page: Any,
        specs: Iterable[CaptureSpec],
        *,
        timeout_ms: int = 30_000,
    ):
        self.page = page
        self.specs = tuple(specs)
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise CaptureConfigurationError("capture timeout_ms must be a positive integer.")
        self.timeout_ms = timeout_ms
        self.values: dict[str, Any] = {}
        self.errors: dict[str, str] = {}
        self._claimed_network: set[str] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._network_ready = {
            spec.name: asyncio.Event()
            for spec in self.specs
            if spec.type == "network_json"
        }
        self._installed = False

    def install(self) -> None:
        if self._installed or not any(spec.type == "network_json" for spec in self.specs):
            return
        on = getattr(self.page, "on", None)
        if not callable(on):
            message = "Page does not support passive response capture."
            required = [spec for spec in self.specs if spec.type == "network_json" and spec.required]
            if required:
                raise RequiredCaptureError(message)
            for spec in self.specs:
                if spec.type == "network_json":
                    self.errors[spec.name] = message
                    self._network_ready[spec.name].set()
            return
        on("response", self._on_response)
        self._installed = True

    def close(self) -> None:
        if self._installed:
            remove = getattr(self.page, "remove_listener", None)
            if callable(remove):
                remove("response", self._on_response)
            self._installed = False
        for task in tuple(self._tasks):
            task.cancel()

    async def aclose(self) -> None:
        """Detach listeners and drain cancelled response-body tasks."""

        self.close()
        tasks = tuple(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _on_response(self, response: Any) -> None:
        request = _property(response, "request")
        method = str(_property(request, "method", "")).upper()
        resource_type = str(_property(request, "resource_type", "")).lower()
        status = int(_property(response, "status", 0) or 0)
        url = str(_property(response, "url", ""))
        headers = _property(response, "headers", {}) or {}
        content_type = ""
        if isinstance(headers, Mapping):
            content_type = str(headers.get("content-type", headers.get("Content-Type", ""))).lower()

        for spec in self.specs:
            if spec.type != "network_json" or spec.name in self._claimed_network:
                continue
            if method != "GET" or resource_type not in {"xhr", "fetch"} or not 200 <= status < 300:
                continue
            if not fnmatch.fnmatchcase(url, spec.url_glob):
                continue
            if "json" not in content_type:
                continue
            self._claimed_network.add(spec.name)
            if isinstance(headers, Mapping):
                raw_length = headers.get("content-length", headers.get("Content-Length"))
                try:
                    content_length = int(raw_length) if raw_length is not None else None
                except (TypeError, ValueError):
                    content_length = None
                if content_length is not None and content_length > spec.max_bytes:
                    self.errors[spec.name] = f"response exceeds max_bytes={spec.max_bytes}"
                    self._network_ready[spec.name].set()
                    continue
            task = asyncio.create_task(self._consume_response(spec, response))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _consume_response(self, spec: CaptureSpec, response: Any) -> None:
        try:
            body_method = getattr(response, "body", None)
            if not callable(body_method):
                raise ValueError("response body is unavailable")
            body = await body_method()
            raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
            if len(raw) > spec.max_bytes:
                raise ValueError(f"response exceeds max_bytes={spec.max_bytes}")
            self.values[spec.name] = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self.errors[spec.name] = str(exc)
        finally:
            self._network_ready[spec.name].set()

    async def _collect_embedded(self) -> None:
        for spec in self.specs:
            if spec.type != "embedded_json":
                continue
            try:
                element = await self.page.query_selector(spec.selector)
                if element is None:
                    raise ValueError(f"selector did not match: {spec.selector}")
                text_content = getattr(element, "text_content", None)
                if callable(text_content):
                    raw_value = await text_content()
                else:
                    raw_value = await element.inner_text()
                raw = str(raw_value or "").encode("utf-8")
                if len(raw) > spec.max_bytes:
                    raise ValueError(f"embedded JSON exceeds max_bytes={spec.max_bytes}")
                self.values[spec.name] = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                self.errors[spec.name] = str(exc)

    async def _finish(self) -> None:
        # Let response callbacks already queued by Playwright schedule their body
        # reads. Required network captures then wait within the page's bounded
        # capture timeout for a delayed XHR/fetch response to appear.
        await asyncio.sleep(0)
        await self._collect_embedded()
        required_network = [
            self._network_ready[spec.name].wait()
            for spec in self.specs
            if spec.type == "network_json"
            and spec.required
            and spec.name not in self.values
            and spec.name not in self.errors
        ]
        if required_network:
            await asyncio.gather(*required_network)
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks))
            await asyncio.sleep(0)

    async def finish(self) -> dict[str, Any]:
        try:
            await asyncio.wait_for(
                self._finish(),
                timeout=self.timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            for task in tuple(self._tasks):
                task.cancel()
            if self._tasks:
                await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
            message = f"capture timed out after {self.timeout_ms}ms"
            for spec in self.specs:
                if spec.name not in self.values:
                    self.errors.setdefault(spec.name, message)

        missing = []
        for spec in self.specs:
            if spec.required and spec.name not in self.values:
                detail = self.errors.get(spec.name, "no matching capture")
                missing.append(f"{spec.name}: {detail}")
        if missing:
            raise RequiredCaptureError("Required captures failed: " + "; ".join(missing))
        return dict(self.values)


__all__ = [
    "CaptureConfigurationError",
    "CaptureSpec",
    "DEFAULT_MAX_BYTES",
    "MAX_CAPTURE_BYTES",
    "PageCaptureSession",
    "RequiredCaptureError",
    "parse_capture_specs",
]
