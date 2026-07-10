from __future__ import annotations

import asyncio
import sys
import types
import unittest
from unittest.mock import patch

from research_assistant.providers import GeminiProvider, OpenAICompatibleProvider, ProviderError, QwenProvider, parse_json_object
from research_assistant.settings import ProviderConfig


class FakeHttpResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {"choices": [{"message": {"content": '{"summary": "ok"}'}}]}


class FakeHttpClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return FakeHttpResponse()


class ResearchAssistantProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_compatible_adapter_uses_unified_json_protocol(self) -> None:
        config = ProviderConfig(
            name="openai",
            kind="openai_compatible",
            model="test-model",
            secret_ref="provider:test",
            base_url="https://example.com/v1",
        )
        provider = OpenAICompatibleProvider(config, "test-key")
        with patch("httpx.AsyncClient", FakeHttpClient):
            response = await provider.complete_json("system", "user")
        self.assertEqual(provider.endpoint, "https://example.com/v1/chat/completions")
        self.assertEqual(response["summary"], "ok")

    async def test_gemini_and_qwen_adapters_return_text(self) -> None:
        http_options_seen = []

        class HttpOptions:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                http_options_seen.append(kwargs)

        class GeminiModels:
            def generate_content(self, **kwargs):
                return types.SimpleNamespace(text="{\"summary\": \"gemini\"}")

        google_module = types.ModuleType("google")
        genai_module = types.ModuleType("google.genai")
        genai_module.Client = lambda **kwargs: types.SimpleNamespace(models=GeminiModels())
        google_module.genai = genai_module
        types_module = types.ModuleType("google.genai.types")
        types_module.HttpOptions = HttpOptions
        qwen_calls = []
        dashscope_module = types.ModuleType("dashscope")
        dashscope_module.Generation = types.SimpleNamespace(
            call=lambda **kwargs: qwen_calls.append(kwargs)
            or types.SimpleNamespace(output=types.SimpleNamespace(choices=[{"message": {"content": "{\"summary\": \"qwen\"}"}}]))
        )
        gemini_config = ProviderConfig("gemini", "gemini", "gemini-test", "provider:g", endpoint="https://gemini.example")
        qwen_config = ProviderConfig("qwen", "qwen", "qwen-test", "provider:q", endpoint="https://qwen.example")
        with patch.dict(
            sys.modules,
            {"google": google_module, "google.genai": genai_module, "google.genai.types": types_module, "dashscope": dashscope_module},
        ):
            self.assertEqual((await GeminiProvider(gemini_config, "key").complete_json("s", "u"))["summary"], "gemini")
            self.assertEqual((await QwenProvider(qwen_config, "key").complete_json("s", "u"))["summary"], "qwen")
        self.assertEqual(http_options_seen[0]["base_url"], "https://gemini.example")
        self.assertEqual(qwen_calls[0]["base_address"], "https://qwen.example")

    async def test_provider_timeout_becomes_a_safe_error(self) -> None:
        config = ProviderConfig("qwen", "qwen", "qwen-test", "provider:q", timeout_seconds=0.1)
        dashscope_module = types.ModuleType("dashscope")
        dashscope_module.Generation = types.SimpleNamespace(call=lambda **kwargs: None)

        async def timed_out(awaitable, *args, **kwargs):
            awaitable.close()
            raise asyncio.TimeoutError

        with patch.dict(sys.modules, {"dashscope": dashscope_module}), patch(
            "research_assistant.providers.asyncio.wait_for", new=timed_out
        ):
            with self.assertRaises(ProviderError):
                await QwenProvider(config, "key").complete("s", "u")

    def test_invalid_json_is_not_silently_accepted(self) -> None:
        self.assertEqual(parse_json_object("```json\n{\"summary\": \"ok\"}\n```"), {"summary": "ok"})
        with self.assertRaises(ProviderError):
            parse_json_object("model refusal")
