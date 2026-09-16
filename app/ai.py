"""Optional AI fallback (Gemini API free tier) for unknown merchants.

Privacy contract:
* only *normalized merchant keys* of unknown merchants are sent — never amounts,
  dates, balances or the full statement;
* the API key comes from the environment only;
* the response is validated with Pydantic and restricted to the allowed
  category list; anything else is discarded;
* any network / timeout / parsing problem degrades to "no suggestion".

The HTTP transport is injectable so tests never touch the real API.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.categories import EXPENSE_CATEGORIES, OTHER
from app.config import settings

log = logging.getLogger(__name__)

Transport = Callable[[str, dict[str, Any], float], dict[str, Any]]
"""(url, json_body, timeout) -> parsed JSON response. Raises on failure."""

UNAVAILABLE_MESSAGE = "AI suggestions unavailable"


class Suggestion(BaseModel):
    merchant: str = Field(min_length=1)
    category: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("category")
    @classmethod
    def _category_allowed(cls, value: str) -> str:
        value = value.strip()
        for allowed in EXPENSE_CATEGORIES:
            if value.lower() == allowed.lower():
                return allowed
        raise ValueError(f"category '{value}' is not in the allowed list")


_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "suggestions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "merchant": {"type": "STRING"},
                    "category": {"type": "STRING", "enum": list(EXPENSE_CATEGORIES)},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["merchant", "category", "confidence"],
            },
        }
    },
    "required": ["suggestions"],
}


def _default_transport(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=body)
        response.raise_for_status()
        return response.json()


def build_prompt(merchants: list[str]) -> str:
    cats = "\n".join(f"- {c}" for c in EXPENSE_CATEGORIES)
    lines = "\n".join(f"- {m}" for m in merchants)
    return (
        "You categorize business expense merchants for a freelancer / small business bookkeeping tool.\n"
        "For each merchant name below choose exactly one category from this list (use the exact spelling):\n"
        f"{cats}\n\n"
        f"Use '{OTHER}' when unsure and give a low confidence (0..1). "
        'Return JSON only: {"suggestions": [{"merchant", "category", "confidence"}]}. '
        "Echo each merchant name exactly as given.\n\n"
        f"Merchants:\n{lines}"
    )


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise ValueError("no candidates in response")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    if not text.strip():
        raise ValueError("empty response text")
    return text


class GeminiClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else settings.gemini_api_key).strip()
        self.model = model or settings.gemini_model
        self.timeout = timeout or settings.ai_timeout_seconds
        self._transport = transport or _default_transport

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def suggest(self, merchants: list[str]) -> dict[str, Suggestion]:
        """Return {merchant_key: Suggestion}. Empty dict on any failure."""
        merchants = sorted({m for m in merchants if m})
        if not merchants or not self.available:
            return {}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        body = {
            "contents": [{"role": "user", "parts": [{"text": build_prompt(merchants)}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                "responseSchema": _RESPONSE_SCHEMA,
            },
        }
        try:
            payload = self._transport(url, body, self.timeout)
            text = _extract_text(payload)
            data = json.loads(text)
            raw_items = data.get("suggestions") if isinstance(data, dict) else None
            if not isinstance(raw_items, list):
                raise ValueError("response has no 'suggestions' list")
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # Never log merchant names or payloads — only the failure class.
            log.warning("AI suggestion request failed: %s", type(exc).__name__)
            return {}

        wanted = {m.upper(): m for m in merchants}
        result: dict[str, Suggestion] = {}
        for item in raw_items:
            try:
                s = Suggestion.model_validate(item)
            except ValidationError:
                continue  # drop invalid / disallowed entries, keep the rest
            key = wanted.get(s.merchant.strip().upper())
            if key is not None:
                result[key] = s
        return result
