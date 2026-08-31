"""app/services/qra_engine.py post-split: the actual type P/T/B QRA
business logic now lives entirely in catalog-service (this backend's copy
was deleted along with qra_header/qra_detail - see catalog-service's own
app/services/qra_engine.py for that logic and its tests). This backend's
qra_engine.py is now a thin, read-only wrapper around
catalog_client.preview_qra() that exists only so callers written against
the pre-split local signature (session, cust_nb, lines, is_return) don't
all need to change. These tests cover that forwarding/reshaping, not any
QRA math.
"""
from decimal import Decimal

from app.services import catalog_client, qra_engine


class _Line:
    def __init__(self, line_nb, item_nb="1001", item_desc="Widget",
                category="cat", qty=Decimal("2"), uom="EA"):
        self.line_nb = line_nb
        self.item_nb = item_nb
        self.item_desc = item_desc
        self.category = category
        self.qty = qty
        self.uom = uom


def test_preview_qra_forwards_to_catalog_client_and_reshapes(monkeypatch):
    captured = {}

    def fake_preview_qra(cust_nb, lines, is_return=False):
        captured["cust_nb"] = cust_nb
        captured["lines"] = lines
        captured["is_return"] = is_return
        return (
            [catalog_client.QraLinePreview(
                line_nb=1, unit_price=Decimal("8.00"), is_free=False,
                substituted_item_nb="1002", substituted_item_desc="Sub Widget")],
            [catalog_client.QraBonusLinePreview(
                item_nb="1003", item_desc="Bonus Widget",
                qty=Decimal("1"), uom="EA")],
        )

    monkeypatch.setattr(catalog_client, "preview_qra", fake_preview_qra)

    lines = [_Line(1)]
    previews, bonuses = qra_engine.preview_qra(session=None, cust_nb="58466",
                                               lines=lines, is_return=False)

    assert captured == {"cust_nb": "58466", "lines": lines, "is_return": False}

    assert len(previews) == 1
    p = previews[0]
    assert isinstance(p, qra_engine.QraLinePreview)
    assert p.line_nb == 1
    assert p.unit_price == Decimal("8.00")
    assert p.is_free is False
    assert p.substituted_item_nb == "1002"
    assert p.substituted_item_desc == "Sub Widget"

    assert len(bonuses) == 1
    b = bonuses[0]
    assert isinstance(b, qra_engine.QraBonusLinePreview)
    assert b.item_nb == "1003"
    assert b.qty == Decimal("1")


def test_preview_qra_session_argument_is_ignored(monkeypatch):
    """Accepted only for call-site compatibility with the pre-split local
    signature - passing a real session (or None, as here) must not matter,
    since nothing in this function touches the DB anymore."""
    monkeypatch.setattr(catalog_client, "preview_qra",
                        lambda cust_nb, lines, is_return=False: ([], []))
    previews, bonuses = qra_engine.preview_qra(
        session="not-a-real-session", cust_nb=None, lines=[])
    assert previews == []
    assert bonuses == []


def test_preview_qra_empty_lines_returns_empty(monkeypatch):
    called = []
    monkeypatch.setattr(catalog_client, "preview_qra",
                        lambda cust_nb, lines, is_return=False:
                            called.append(1) or ([], []))
    previews, bonuses = qra_engine.preview_qra(session=None, cust_nb="58466",
                                               lines=[])
    assert previews == [] and bonuses == []
    assert called == [1]
