from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, Dict
from urllib.parse import urlparse

from .secrets import SecretStore
from .settings import ProviderConfig


class ProviderError(RuntimeError):
    pass


class ModelProvider(ABC):
    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError

    async def complete_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        text = await self.complete(system_prompt, user_prompt)
        return parse_json_object(text)


def parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3:
            cleaned = "\n".join(lines[1:-1]).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ProviderError("Model response did not contain a JSON object.")
    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ProviderError("Model returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise ProviderError("Model JSON response must be an object.")
    return value


class OpenAICompatibleProvider(ModelProvider):
    def __init__(self, config: ProviderConfig, api_key: str):
        if not config.base_url:
            raise ProviderError("openai_compatible provider requires base_url.")
        parsed = urlparse(config.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderError("base_url must be an absolute http(s) URL.")
        self.config = config
        self.api_key = api_key

    @property
    def endpoint(self) -> str:
        base = self.config.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            import httpx
        except Exception as exc:  # pragma: no cover
            raise ProviderError("httpx is required for OpenAI-compatible providers.") from exc

        payload = {
            "model": self.config.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        timeout = self.config.timeout_seconds
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            try:
                response = await client.post(self.endpoint, headers=headers, json=payload)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderError(f"OpenAI-compatible request failed: {exc}") from exc
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("OpenAI-compatible response had no message content.") from exc
        if not isinstance(content, str):
            raise ProviderError("OpenAI-compatible response content was not text.")
        return content


class GeminiProvider(ModelProvider):
    def __init__(self, config: ProviderConfig, api_key: str):
        self.config = config
        self.api_key = api_key

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            from google import genai  # type: ignore[import]
            from google.genai import types  # type: ignore[import]
        except Exception as exc:  # pragma: no cover
            raise ProviderError("google-genai is required for Gemini providers.") from exc

        options: Dict[str, Any] = {"timeout": int(self.config.timeout_seconds * 1000)}
        if self.config.endpoint:
            options["base_url"] = self.config.endpoint
        client = genai.Client(api_key=self.api_key, http_options=types.HttpOptions(**options))
        prompt = f"{system_prompt}\n\n{user_prompt}"
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model=self.config.model,
                    contents=prompt,
                ),
                timeout=self.config.timeout_seconds + 2,
            )
        except asyncio.TimeoutError as exc:
            raise ProviderError(f"Gemini request timed out after {self.config.timeout_seconds} seconds.") from exc
        except Exception as exc:
            raise ProviderError(f"Gemini request failed: {exc}") from exc
        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("Gemini response had no text content.")
        return text


class QwenProvider(ModelProvider):
    def __init__(self, config: ProviderConfig, api_key: str):
        self.config = config
        self.api_key = api_key

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            import dashscope  # type: ignore[import]
        except Exception as exc:  # pragma: no cover
            raise ProviderError("dashscope is required for Qwen providers.") from exc
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            kwargs: Dict[str, Any] = {
                "api_key": self.api_key,
                "model": self.config.model,
                "messages": messages,
                "result_format": "message",
                "temperature": 0.0,
            }
            if self.config.endpoint:
                kwargs["base_address"] = self.config.endpoint
            response = await asyncio.wait_for(
                asyncio.to_thread(dashscope.Generation.call, **kwargs),
                timeout=self.config.timeout_seconds + 2,
            )
        except asyncio.TimeoutError as exc:
            raise ProviderError(f"Qwen request timed out after {self.config.timeout_seconds} seconds.") from exc
        except Exception as exc:
            raise ProviderError(f"Qwen request failed: {exc}") from exc
        output = getattr(response, "output", None)
        choices = getattr(output, "choices", None) if output is not None else None
        if not choices:
            raise ProviderError("Qwen response had no choices.")
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("Qwen response had no text content.")
        return content


class StaticProvider(ModelProvider):
    """Deterministic provider used by offline tests."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.requests: list[tuple[str, str]] = []

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.requests.append((system_prompt, user_prompt))
        if not self.responses:
            raise ProviderError("StaticProvider has no response left.")
        return self.responses.pop(0)


def create_provider(config: ProviderConfig, secret_store: SecretStore) -> ModelProvider:
    api_key = secret_store.get(config.secret_ref)
    if config.kind == "openai_compatible":
        return OpenAICompatibleProvider(config, api_key)
    if config.kind == "gemini":
        return GeminiProvider(config, api_key)
    if config.kind == "qwen":
        return QwenProvider(config, api_key)
    raise ProviderError(f"Unsupported provider kind: {config.kind}")
