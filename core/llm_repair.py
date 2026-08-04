from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


class LLMRepair:
    """
    根据失败的 CSS 选择器调用模型修复新的选择器。

    支持 provider:
      - ollama (local process; no credential or cloud service required)
      - gemini (google-genai)
      - qwen   (dashscope Qwen-VL/Qwen-VL-Plus)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        raw_enable_repair = self.config.get("enable_repair", False)
        if not isinstance(raw_enable_repair, bool):
            raise TypeError("llm.enable_repair must be true or false.")
        self.enable_repair = raw_enable_repair
        self.provider = str(self.config.get("provider", "ollama")).strip().lower() or "ollama"
        self.secret_ref = str(self.config.get("secret_ref", "") or "").strip()
        self.api_key = self._load_api_key() if self.enable_repair else ""
        self.model = str(self.config.get("model", "") or "").strip()
        self.endpoint = str(self.config.get("endpoint", "") or "").strip()
        self.timeout = float(self.config.get("timeout", 10) or 10)
        self.selector_cache: Dict[str, str] = {}

        if not self.model:
            self.model = {
                "gemini": "gemini-2.5-flash",
                "qwen": "qwen-vl-plus",
                "ollama": "qwen3",
            }.get(self.provider, "qwen3")

        # 可选导入，仅在对应 provider 开启时加载，避免不必要依赖报错打断初始化。
        self._gemini_client = None
        self._dashscope = None
        if self.enable_repair:
            if self.provider == "gemini":
                try:
                    from google import genai  # type: ignore[import]
                    from google.genai import types  # type: ignore[import]

                    http_options: Dict[str, Any] = {"timeout": int(self.timeout * 1000)}
                    if self.endpoint:
                        http_options["base_url"] = self.endpoint
                    self._gemini_client = genai.Client(
                        api_key=self.api_key,
                        http_options=types.HttpOptions(**http_options),
                    )
                    logger.debug("LLMRepair initialized with provider=gemini.")
                except Exception as exc:
                    logger.warning("google-genai 初始化失败，Gemini 将不可用：%s", exc)
            elif self.provider == "qwen":
                try:
                    import dashscope  # type: ignore[import]

                    self._dashscope = dashscope
                    logger.debug("LLMRepair initialized with provider=qwen.")
                except Exception as exc:
                    logger.warning("dashscope 初始化失败，Qwen 将不可用：%s", exc)

    def _load_api_key(self) -> str:
        """Resolve the preferred credential-store reference without persisting a key in YAML.

        `api_key` remains a temporary compatibility path for code that constructs
        LLMRepair directly. New YAML configs should use `secret_ref`; the agent
        refuses inline keys altogether before it reaches this class.
        """
        if self.secret_ref:
            try:
                from research_assistant.secrets import KeyringSecretStore

                return KeyringSecretStore().get(self.secret_ref)
            except Exception as exc:
                logger.warning("无法从系统凭据库读取 secret_ref=%s：%s", self.secret_ref, exc)
                return ""

        inline_key = str(self.config.get("api_key", "") or "").strip()
        if inline_key and inline_key not in {"YOUR_API_KEY", "<YOUR_API_KEY>"}:
            logger.warning("llm.api_key 已弃用；请改用系统凭据库中的 llm.secret_ref。")
            return inline_key
        return ""

    def cache_key(
        self,
        page_url: str,
        field_name: str,
        failed_selector: str = "",
        context: str = "",
    ) -> str:
        page_version = hashlib.sha256(
            self._truncate_html(context).encode("utf-8")
        ).hexdigest()
        return f"{page_url}::{field_name}::{failed_selector}::{page_version}"

    @staticmethod
    def _truncate_html(html: str, max_len: int = 3000) -> str:
        if not html:
            return ""
        normalized = html.strip()
        if len(normalized) <= max_len:
            return normalized
        return normalized[:max_len] + "\n... [truncated] ..."

    @staticmethod
    def _extract_selector(text: str) -> str:
        if not text:
            return ""

        cleaned = text.strip()
        fenced = re.search(r"```(?:css)?\s*(.*?)```", cleaned, flags=re.S | re.I)
        if fenced:
            cleaned = fenced.group(1).strip()
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if not lines:
            return ""

        for line in lines:
            candidate = line.strip().strip("`").strip()
            if candidate.lower().startswith("css") and ":" in candidate:
                candidate = candidate.split(":", 1)[1].strip()
            if candidate:
                candidate = candidate.strip("` ").strip('"').strip("'").rstrip(";")
                if len(candidate) > 500:
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_#.[\]=\"'():>+~*^$|,\\\-\s]+", candidate):
                    continue
                return candidate
        return ""

    async def repair_selector(
        self,
        page: Any,
        field_name: str,
        field_description: str,
        failed_selector: str,
        context_html_or_screenshot: Any = "",
    ) -> str:
        if not self.enable_repair:
            return ""

        if self.provider != "ollama" and not self.api_key:
            logger.debug("LLMRepair 未配置 api_key，跳过修复。")
            return ""

        if not field_name:
            return ""

        page_url = ""
        try:
            page_url = str(getattr(page, "url", ""))
        except Exception:
            page_url = ""

        html_or_screenshot = context_html_or_screenshot or ""
        if not html_or_screenshot:
            try:
                html_or_screenshot = await page.content()
            except Exception as exc:  # pragma: no cover
                logger.debug("LLMRepair 获取页面内容失败：%s", exc)
                html_or_screenshot = ""

        context = html_or_screenshot
        if isinstance(context, bytes):
            try:
                context = base64.b64encode(context).decode("utf-8")
            except Exception:
                context = ""

        context_text = str(context)
        if self.provider in {"gemini", "qwen"}:
            # Remote providers are explicit opt-ins, but their prompt still
            # receives the same credential/session redaction used by episode
            # artifacts. Local Ollama never crosses a network boundary.
            from core.experience_store import sanitize_payload

            sanitized = sanitize_payload(context_text)
            context_text = sanitized if isinstance(sanitized, str) else ""

        key = self.cache_key(page_url, field_name, failed_selector, context_text)
        if key in self.selector_cache:
            return self.selector_cache[key]

        prompt = (
            "当前网页片段如下：\n"
            f"{self._truncate_html(context_text)}\n"
            f"我需要提取\"{field_description}\"的内容，之前的选择器\"{failed_selector}\"已失效，"
            "请给出一个最精准的新CSS选择器，只输出选择器本身。"
        )

        selector: str = ""
        try:
            if self.provider == "qwen" and self._dashscope is not None:
                selector = await asyncio.wait_for(
                    asyncio.to_thread(self._repair_with_qwen, prompt, context_text, page_url),
                    timeout=self.timeout + 2,
                )
            elif self.provider == "gemini" and self._gemini_client is not None:
                selector = await asyncio.wait_for(
                    asyncio.to_thread(self._repair_with_gemini, prompt, context_text, page_url),
                    timeout=self.timeout + 2,
                )
            elif self.provider == "ollama":
                selector = await asyncio.wait_for(
                    self._repair_with_ollama(prompt),
                    timeout=self.timeout + 2,
                )
            else:
                logger.debug("LLM provider=%s 不可用，跳过修复。", self.provider)
                return ""
        except asyncio.TimeoutError:
            logger.warning("LLM 修复选择器超时（%s 秒）: field=%s", self.timeout, field_name)
            return ""
        except Exception as exc:
            logger.warning("LLM 修复请求失败：%s", exc)
            return ""

        selector = self._extract_selector(selector)
        if not selector:
            logger.debug("LLM 返回结果无法解析为选择器：%s", selector)
            return ""

        self.selector_cache[key] = selector
        return selector

    async def _repair_with_ollama(self, prompt: str) -> str:
        """Run the legacy local Ollama provider without a second pipeline."""

        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "ollama",
                "run",
                self.model,
                prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except Exception as exc:
            logger.debug("Local Ollama provider is unavailable: %s", exc)
            return ""
        if process.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace")
            logger.warning(
                "Local Ollama repair failed for model=%s; stderr_bytes=%d sha256=%s",
                self.model,
                len(stderr),
                hashlib.sha256(stderr_text.encode("utf-8")).hexdigest(),
            )
            return ""
        return stdout.decode("utf-8", errors="replace").strip()

    def _repair_with_gemini(self, prompt: str, context: str, page_url: str) -> str:
        client = self._gemini_client
        if client is None:
            return ""

        response = client.models.generate_content(model=self.model, contents=prompt)
        text = getattr(response, "text", None) or ""
        if not text:
            candidates = getattr(response, "candidates", []) or []
            if candidates:
                parts = []
                content = candidates[0].get("content") if isinstance(candidates[0], dict) else getattr(candidates[0], "content", None)
                if content is not None:
                    if isinstance(content, dict):
                        for part in content.get("parts", []) or []:
                            if isinstance(part, dict):
                                parts.append(part.get("text", ""))
                    else:
                        for part in getattr(content, "parts", []) or []:
                            parts.append(getattr(part, "text", ""))
                text = "".join(str(p) for p in parts)
        logger.debug(
            "Gemini returned candidate metadata: chars=%d sha256=%s",
            len(text),
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        return text

    def _repair_with_qwen(self, prompt: str, context: str, page_url: str) -> str:
        if self._dashscope is None:
            return ""
        messages = [
            {
                "role": "system",
                "content": "You are a reliable CSS selector fixer assistant.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]
        kwargs: Dict[str, Any] = {
            "api_key": self.api_key,
            "model": self.model,
            "messages": messages,
            "result_format": "message",
            "max_tokens": 256,
            "temperature": 0.0,
        }
        if self.endpoint:
            kwargs["base_address"] = self.endpoint
        response = self._dashscope.MultiModalConversation.call(**kwargs)
        # 兼容返回结构
        outputs = getattr(response, "output", None)
        if outputs is not None:
            text = getattr(outputs, "text", None) or ""
            if text:
                logger.debug(
                    "Qwen returned candidate metadata: chars=%d sha256=%s",
                    len(str(text)),
                    hashlib.sha256(str(text).encode("utf-8")).hexdigest(),
                )
                return text
            if hasattr(outputs, "choices") and outputs.choices:
                message = outputs.choices[0].get("message") if isinstance(outputs.choices[0], dict) else getattr(outputs.choices[0], "message", None)
                if message:
                    text = message.get("content") if isinstance(message, dict) else str(message)
                    logger.debug(
                        "Qwen returned candidate metadata: chars=%d sha256=%s",
                        len(str(text)),
                        hashlib.sha256(str(text).encode("utf-8")).hexdigest(),
                    )
                    return str(text)
        if isinstance(response, dict):
            out = response.get("output", {})
            if isinstance(out, dict):
                candidate = out.get("text")
                if candidate:
                    logger.debug(
                        "Qwen returned candidate metadata: chars=%d sha256=%s",
                        len(str(candidate)),
                        hashlib.sha256(str(candidate).encode("utf-8")).hexdigest(),
                    )
                    return str(candidate)
        return ""
