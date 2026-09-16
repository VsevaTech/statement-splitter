from __future__ import annotations

import json

import httpx
import pytest

from app import categories as c
from app.ai import GeminiClient, build_prompt
from app.categorize import categorize
from app.normalize import normalize_merchant
from app.schemas import NormalizedTransaction


def _gemini_payload(items) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps({"suggestions": items})}]}}]}


def _tx(desc: str, amount: float = -10.0) -> NormalizedTransaction:
    return NormalizedTransaction(
        row_index=0,
        date=None,
        description=desc,
        normalized_merchant=normalize_merchant(desc),
        amount=amount,
        currency="ILS",
        direction="debit",
    )


def test_ai_disabled_without_key():
    client = GeminiClient(api_key="", transport=lambda *_: pytest.fail("must not call the network"))
    assert not client.available
    assert client.suggest(["CERCLI"]) == {}


def test_ai_not_called_when_use_ai_false():
    calls = []
    client = GeminiClient(api_key="k", transport=lambda *a: calls.append(a) or _gemini_payload([]))
    txs = [_tx("MYSTERY SHOP")]
    categorize(txs, {}, use_ai=False, ai_client=client)
    assert calls == [] and txs[0].category is None


def test_ai_receives_only_merchant_keys():
    captured = {}

    def transport(url, body, timeout):
        captured["url"], captured["body"] = url, body
        return _gemini_payload([{"merchant": "CERCLI", "category": c.ACCOUNTING, "confidence": 0.9}])

    client = GeminiClient(api_key="secret-key", model="gemini-test", transport=transport)
    txs = [_tx("CERCLI LTD 778812", -450.0), _tx("UBER *TRIP 1", -30.0)]
    categorize(txs, {}, use_ai=True, ai_client=client)
    prompt = captured["body"]["contents"][0]["parts"][0]["text"]
    assert "CERCLI" in prompt
    assert "778812" not in prompt and "450" not in prompt  # no raw description, no amounts
    assert "UBER" not in prompt  # already categorized by built-in rule → not sent
    assert "gemini-test" in captured["url"] and "secret-key" in captured["url"]
    assert txs[0].category == c.ACCOUNTING and txs[0].category_source == "ai"
    assert txs[0].confidence == pytest.approx(0.9)


def test_ai_low_confidence_stays_in_review():
    client = GeminiClient(
        api_key="k",
        transport=lambda *_: _gemini_payload([{"merchant": "MYSTERY SHOP", "category": c.OTHER, "confidence": 0.3}]),
    )
    txs = [_tx("MYSTERY SHOP")]
    categorize(txs, {}, use_ai=True, ai_client=client, min_confidence=0.6)
    assert txs[0].category is None and txs[0].category_source == "unknown"


def test_ai_malformed_response_is_ignored():
    client = GeminiClient(
        api_key="k", transport=lambda *_: {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}
    )
    assert client.suggest(["X"]) == {}
    client = GeminiClient(api_key="k", transport=lambda *_: {"weird": True})
    assert client.suggest(["X"]) == {}
    client = GeminiClient(api_key="k", transport=lambda *_: _gemini_payload("nope"))
    assert client.suggest(["X"]) == {}


def test_ai_disallowed_category_is_dropped_but_valid_kept():
    client = GeminiClient(
        api_key="k",
        transport=lambda *_: _gemini_payload(
            [
                {"merchant": "A", "category": "Crypto Gambling", "confidence": 0.99},
                {"merchant": "B", "category": "software / saas", "confidence": 0.8},
                {"merchant": "C", "category": c.INCOME, "confidence": 0.8},  # not an expense category
                {"merchant": "NOT REQUESTED", "category": c.OTHER, "confidence": 0.8},
                {"merchant": "D", "category": c.OTHER, "confidence": 7},  # out of range
            ]
        ),
    )
    result = client.suggest(["A", "B", "C", "D"])
    assert set(result) == {"B"}
    assert result["B"].category == c.SOFTWARE


def test_ai_timeout_degrades_gracefully():
    def transport(*_):
        raise httpx.ReadTimeout("timed out")

    client = GeminiClient(api_key="k", transport=transport)
    txs = [_tx("MYSTERY SHOP")]
    categorize(txs, {}, use_ai=True, ai_client=client)
    assert txs[0].category is None


def test_ai_http_error_degrades_gracefully():
    def transport(*_):
        raise httpx.HTTPStatusError("429", request=httpx.Request("POST", "http://x"), response=httpx.Response(429))

    assert GeminiClient(api_key="k", transport=transport).suggest(["X"]) == {}


def test_prompt_lists_only_allowed_categories():
    prompt = build_prompt(["CERCLI"])
    for cat in c.EXPENSE_CATEGORIES:
        assert cat in prompt
    assert c.INCOME not in prompt
