from __future__ import annotations

import pytest

from app import categories as c
from app.categorize import categorize, load_rules, save_rule
from app.normalize import normalize_merchant
from app.rules import match_builtin
from app.schemas import NormalizedTransaction


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("GOOGLE*GSUITE 839291", "GOOGLE GSUITE 482901"),
        ("UBER *TRIP 123456", "UBER TRIP 998877"),
        ("PAYPAL *SPOTIFY", "SPOTIFY"),
        ("CERCLI LTD 778812", "CERCLI LTD"),
        ("WOLT IL 12/03", "WOLT IL"),
        ("AIRBNB * HMXQ12345", "AIRBNB"),
    ],
)
def test_normalization_merges_same_merchant(a, b):
    assert normalize_merchant(a) == normalize_merchant(b)


def test_normalization_examples():
    assert normalize_merchant("GOOGLE*GSUITE 839291") == "GOOGLE GSUITE"
    assert normalize_merchant("CERCLI LTD 778812") == "CERCLI"
    assert normalize_merchant("WOLT IL") == "WOLT IL"
    assert normalize_merchant("MYSTERY SHOP 42") == "MYSTERY SHOP"  # trailing branch/ref numbers dropped
    assert normalize_merchant("7 ELEVEN STORE") == "7 ELEVEN STORE"  # leading/interior numbers kept
    assert normalize_merchant("365") == "365"  # never collapse to an empty key
    assert normalize_merchant("") == ""


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("GOOGLE GSUITE", "GOOGLE CLOUD"),
        ("WOLT IL", "WOLT DE"),
        ("CAFE NIMROD", "CAFE NOIR"),
        ("PARTNER COMMUNICATION", "PARTNER LOGISTICS"),
    ],
)
def test_normalization_is_conservative(a, b):
    assert normalize_merchant(a) != normalize_merchant(b)


def test_builtin_rules():
    assert match_builtin("UBER TRIP")[0] == c.TRANSPORT
    assert match_builtin("GETT")[0] == c.TRANSPORT
    assert match_builtin("AWS EMEA")[0] == c.CLOUD
    assert match_builtin("GOOGLE CLOUD")[0] == c.CLOUD
    assert match_builtin("BANK FEE ACCOUNT MAINTENANCE")[0] == c.BANK_FEES
    assert match_builtin("FX COMMISSION")[0] == c.BANK_FEES
    assert match_builtin("TOTALLY UNKNOWN SHOP") is None


def _tx(desc: str, amount: float, currency: str = "ILS") -> NormalizedTransaction:
    return NormalizedTransaction(
        row_index=0,
        date=None,
        description=desc,
        normalized_merchant=normalize_merchant(desc),
        amount=amount,
        currency=currency,
        direction="credit" if amount > 0 else "debit",
    )


def test_categorize_levels_without_ai():
    txs = [_tx("UBER *TRIP 1", -30), _tx("MYSTERY SHOP", -10), _tx("CLIENT PAYMENT", 500)]
    categorize(txs, {}, use_ai=False)
    assert (txs[0].category, txs[0].category_source) == (c.TRANSPORT, "builtin")
    assert (txs[1].category, txs[1].category_source) == (None, "unknown")
    assert txs[1].needs_review
    assert (txs[2].category, txs[2].category_source) == (c.INCOME, "income")


def test_saved_rule_beats_builtin():
    txs = [_tx("UBER *TRIP 1", -30)]
    categorize(txs, {"UBER TRIP": c.PERSONAL}, use_ai=False)
    assert (txs[0].category, txs[0].category_source, txs[0].confidence) == (c.PERSONAL, "rule", 1.0)


def test_saved_rule_categorizes_unknown_merchant():
    txs = [_tx("CERCLI LTD 778812", -450)]
    categorize(txs, {"CERCLI": c.ACCOUNTING}, use_ai=False)
    assert txs[0].category == c.ACCOUNTING and txs[0].category_source == "rule"


def test_rule_persistence(session):
    save_rule(session, "CERCLI", c.ACCOUNTING)
    session.commit()
    assert load_rules(session) == {"CERCLI": c.ACCOUNTING}
    save_rule(session, "CERCLI", c.PROFESSIONAL)  # update, not duplicate
    session.commit()
    assert load_rules(session) == {"CERCLI": c.PROFESSIONAL}
    with pytest.raises(ValueError):
        save_rule(session, "X", "Not a category")
    with pytest.raises(ValueError):
        save_rule(session, "  ", c.OTHER)
