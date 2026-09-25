"""LLM client — REUSES the existing EDUnation provider keys and the proven
OpenRouter → Groq fallback chain (same documented APIs as content_creation).
Reads OPENROUTER_API_KEY / GROQ_API_KEY — no new credentials."""
from __future__ import annotations

import json
import re
import time
from typing import Optional, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError as PydanticValidationError

from .config import get_settings
from .exceptions import LLMUnavailableError

T = TypeVar("T", bound=BaseModel)
RETRYABLE = {408, 409, 429, 500, 502, 503, 504}
import time as _time
_OPENROUTER_402_UNTIL = 0.0  # circuit breaker: skip OpenRouter for 10 min after a 402
GROQ_PREFERRED = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b",
                  "qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"]


class LLMResult:
    def __init__(self, content: str, provider: str, model: str) -> None:
        self.content, self.provider, self.model = content, provider, model


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except ValueError:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", match.group(0)))
        except ValueError:
            return None


class LLMClient:
    """OpenRouter primary → Groq fallback. Structured JSON with one repair
    retry (Groq JSON mode is the workhorse — proven in this project)."""

    def __init__(self) -> None:
        self._groq_models: Optional[set] = None

    # ------------------------------------------------------------- groq
    async def _groq_model(self) -> str:
        settings = get_settings()
        if self._groq_models is None:
            self._groq_models = set()
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.get(
                        "https://api.groq.com/openai/v1/models",
                        headers={"Authorization": f"Bearer {settings.groq_api_key}"})
                if resp.status_code == 200:
                    self._groq_models = {m["id"] for m in resp.json().get("data", [])}
            except httpx.HTTPError:
                pass
        if settings.groq_model in self._groq_models:
            return settings.groq_model
        for candidate in GROQ_PREFERRED:
            if candidate in (self._groq_models or set()):
                return candidate
        return settings.groq_model

    async def _groq_chat(self, messages: list[dict], max_tokens: int,
                          temperature: float, json_mode: bool) -> tuple[str, str]:
        settings = get_settings()
        model = await self._groq_model()
        payload: dict = {"model": model, "messages": messages,
                         "max_tokens": max_tokens, "temperature": temperature}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        import asyncio as _asyncio
        for attempt in range(3):
            async with httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_timeout, connect=15.0)) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    json=payload)
            if resp.status_code == 429 and attempt < 2:
                await _asyncio.sleep(20 * (attempt + 1))  # free-tier rate limit
                continue
            break
        if resp.status_code >= 400:
            raise RuntimeError(f"groq {resp.status_code}")
        return resp.json()["choices"][0]["message"]["content"], model

    # ------------------------------------------------------- openrouter
    async def _openrouter_chat(self, messages: list[dict], max_tokens: int,
                               temperature: float) -> tuple[str, str]:
        global _OPENROUTER_402_UNTIL
        if _time.time() < _OPENROUTER_402_UNTIL:
            raise RuntimeError("openrouter skipped (insufficient credits)")
        settings = get_settings()
        async with httpx.AsyncClient(
                base_url=settings.openrouter_base,
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}",
                         "HTTP-Referer": "https://edunation.app",
                         "X-Title": "EDUnation Assessment"},
                timeout=httpx.Timeout(settings.llm_timeout, connect=15.0)) as client:
            resp = await client.post("/chat/completions", json={
                "model": settings.openrouter_model, "messages": messages,
                "max_tokens": max_tokens, "temperature": temperature})
        if resp.status_code == 402:
            _OPENROUTER_402_UNTIL = _time.time() + 600  # 10-min circuit breaker
            raise RuntimeError("openrouter 402 insufficient credits")
        if resp.status_code >= 400:
            raise RuntimeError(f"openrouter {resp.status_code}")
        return (resp.json()["choices"][0]["message"]["content"],
                settings.openrouter_model)

    # ----------------------------------------------------------- public
    async def chat(self, messages: list[dict], *, max_tokens: int = 2000,
                   temperature: float = 0.3) -> LLMResult:
        settings = get_settings()
        errors: list[str] = []
        # Groq first: it is the configured, working provider. OpenRouter stays
        # as automatic fallback for when its credits are topped up.
        for provider_name, call in (
                ("groq", lambda: self._groq_chat(messages, max_tokens, temperature, False)),
                ("openrouter", lambda: self._openrouter_chat(messages, max_tokens, temperature))):
            if provider_name == "openrouter" and not settings.openrouter_api_key:
                continue
            if provider_name == "groq" and not settings.groq_api_key:
                continue
            try:
                content, model = await call()
                return LLMResult(content, provider_name, model)
            except Exception as exc:  # noqa: BLE001 — chain to next provider
                errors.append(f"{provider_name}: {str(exc)[:120]}")
        if errors:
            # providers ARE configured but all failed (rate limit / quota / outage)
            raise LLMUnavailableError(
                "AI providers are temporarily unavailable (rate limit or quota on "
                "all configured providers). Your work is saved — please retry in "
                "a minute.",
                detail_log="; ".join(errors))
        raise LLMUnavailableError(detail_log="no provider configured")

    async def structured(self, messages: list[dict], schema: Type[T], *,
                         max_tokens: int = 2500) -> T:
        """JSON-validated generation: Groq JSON mode primary, OpenRouter with
        one schema-repair retry as fallback."""
        settings = get_settings()
        schema_json = json.dumps(schema.model_json_schema())[:2200]
        attempts: list[str] = []

        if settings.groq_api_key:
            request = messages + [{"role": "user",
                                   "content": "Reply with ONLY valid JSON matching this schema: " + schema_json}]
            for attempt in range(2):
                try:
                    content, _ = await self._groq_chat(request, max_tokens,
                                                       0.2 if attempt else 0.3, True)
                    parsed = _extract_json(content)
                    if parsed is not None:
                        return schema.model_validate(parsed)
                    attempts.append("groq: unparseable JSON")
                except Exception as exc:  # noqa: BLE001
                    attempts.append(f"groq: {str(exc)[:100]}")

        if settings.openrouter_api_key:
            repair = ""
            for attempt in range(2):
                try:
                    request = messages + ([{"role": "user", "content": repair}] if repair else []) + \
                        [{"role": "user", "content": "Reply with ONLY valid JSON matching this schema: " + schema_json}]
                    content, _ = await self._openrouter_chat(request, max_tokens, 0.2)
                    parsed = _extract_json(content)
                    if parsed is not None:
                        return schema.model_validate(parsed)
                    repair = "Your previous reply was not valid JSON. Reply again with ONLY the JSON object."
                    attempts.append("openrouter: unparseable JSON")
                except Exception as exc:  # noqa: BLE001
                    attempts.append(f"openrouter: {str(exc)[:100]}")

        if attempts:
            raise LLMUnavailableError(
                "AI providers are temporarily unavailable (rate limit or quota on "
                "all configured providers). Your work is saved — please retry in "
                "a minute.",
                detail_log="; ".join(attempts))
        raise LLMUnavailableError(detail_log="no provider configured")


llm = LLMClient()
