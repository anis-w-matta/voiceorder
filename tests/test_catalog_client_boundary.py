"""app/services/catalog_client.py's own request/response shaping - the one
place this suite talks to a fake HTTP layer (httpx.MockTransport) instead
of monkeypatching the client's functions outright, since everywhere else
just needs "catalog-service said X" without caring how X got serialized.
No live catalog-service instance is started for these.
"""
from decimal import Decimal

import httpx
import pytest

from app.errors import (CustomerNotAuthorized, CustomerNotFound,
                        OrderAlreadyReturned, TargetOrderNotFound,
                        UnresolvedLines)
from app.services import catalog_client
from app.services.catalog_client import CommitTransientError


def _mock_client(handler, monkeypatch):
    def _client():
        return httpx.Client(base_url="http://testserver",
                            transport=httpx.MockTransport(handler))
    monkeypatch.setattr(catalog_client, "_client", _client)


class TestGetCustomerDetail:
    def test_parses_full_detail(self, monkeypatch):
        def handler(request):
            assert request.url.path == "/customers/58466"
            return httpx.Response(200, json={
                "cust_nb": "58466", "customer_name": "Economena Analytics sarl",
                "email": None, "telephone": "+96170000000", "city": "Beirut",
                "address1": "Somewhere", "salesman_id": "sm1"})

        _mock_client(handler, monkeypatch)
        detail = catalog_client.get_customer_detail("58466")
        assert detail.cust_nb == "58466"
        assert detail.salesman_id == "sm1"

    def test_null_body_means_not_found(self, monkeypatch):
        _mock_client(lambda request: httpx.Response(
            200, content=b"null", headers={"content-type": "application/json"}), monkeypatch)
        assert catalog_client.get_customer_detail("nope") is None


class TestPreviewQra:
    def test_parses_lines_and_bonus_lines_with_decimal_qty(self, monkeypatch):
        def handler(request):
            assert request.url.path == "/qra/preview"
            return httpx.Response(200, json={
                "lines": [{"line_nb": 1, "unit_price": "8.00", "is_free": False,
                          "substituted_item_nb": None, "substituted_item_desc": None}],
                "bonus_lines": [{"item_nb": "1003", "item_desc": "Bonus",
                                "qty": "1.000", "uom": "EA"}],
            })

        _mock_client(handler, monkeypatch)

        class Line:
            line_nb, item_nb, item_desc, category, qty, uom = (
                1, "1001", "Widget", "cat", Decimal("2"), "EA")

        previews, bonuses = catalog_client.preview_qra("58466", [Line()])
        # SUSPICIOUS: QraLinePreview.unit_price is typed Decimal | None, but
        # preview_qra() assigns the raw JSON value (a str) straight through
        # with no Decimal(...) conversion, unlike every other money/qty
        # field in this module (see create_order()'s _dec() helper, or
        # bonus.qty below, which IS converted). Documenting actual current
        # behavior here, not fixing it - a caller doing unit_price + x or
        # comparing to a Decimal would break today.
        assert previews[0].unit_price == "8.00"
        assert isinstance(previews[0].unit_price, str)
        assert bonuses[0].qty == Decimal("1.000")

    def test_empty_lines_returns_empty_without_a_request(self, monkeypatch):
        def handler(request):
            raise AssertionError("must not call catalog-service for empty lines")

        _mock_client(handler, monkeypatch)
        assert catalog_client.preview_qra("58466", []) == ([], [])


class TestCreateOrderErrorMapping:
    """The 422 typed-error-code -> exception mapping is the contract
    commit.py depends on to distinguish a definitive validation failure
    from something transient/retryable - see OrderCommitService.commit().
    """

    def _call(self, monkeypatch, code, detail="boom"):
        def handler(request):
            return httpx.Response(422, json={"detail": {"code": code, "detail": detail}})

        _mock_client(handler, monkeypatch)
        return catalog_client.create_order(
            commit_intent_id="intent-1", order_type="SO", cust_nb="58466",
            cust_nb_override=None, target_order_nb_override=None,
            primary_intent="add_order", full_return=False, lines=[],
            line_edits=[], removed_line_nbs=[], is_return=False,
            acting_salesman_id="sm1")

    @pytest.mark.parametrize("code,exc_type", [
        ("customer_not_found", CustomerNotFound),
        ("customer_not_authorized", CustomerNotAuthorized),
        ("target_order_not_found", TargetOrderNotFound),
        ("order_already_returned", OrderAlreadyReturned),
        ("unresolved_lines", UnresolvedLines),
    ])
    def test_known_error_code_raises_typed_exception(self, monkeypatch, code, exc_type):
        with pytest.raises(exc_type):
            self._call(monkeypatch, code)

    def test_unrecognized_error_code_is_transient(self, monkeypatch):
        with pytest.raises(CommitTransientError):
            self._call(monkeypatch, "some_future_code_this_client_does_not_know")

    def test_network_failure_is_transient(self, monkeypatch):
        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        _mock_client(handler, monkeypatch)
        with pytest.raises(CommitTransientError):
            catalog_client.create_order(
                commit_intent_id="intent-1", order_type="SO", cust_nb="58466",
                cust_nb_override=None, target_order_nb_override=None,
                primary_intent="add_order", full_return=False, lines=[],
                line_edits=[], removed_line_nbs=[], is_return=False,
                acting_salesman_id="sm1")

    def test_unexpected_5xx_is_transient(self, monkeypatch):
        _mock_client(lambda request: httpx.Response(500, text="boom"), monkeypatch)
        with pytest.raises(CommitTransientError):
            catalog_client.create_order(
                commit_intent_id="intent-1", order_type="SO", cust_nb="58466",
                cust_nb_override=None, target_order_nb_override=None,
                primary_intent="add_order", full_return=False, lines=[],
                line_edits=[], removed_line_nbs=[], is_return=False,
                acting_salesman_id="sm1")

    def test_success_parses_result(self, monkeypatch):
        def handler(request):
            return httpx.Response(200, json={
                "order_nb": "260000123", "order_type": "SO", "cust_nb": "58466",
                "target_order_nb": None, "target_order_type": None,
                "lines": [{"line_nb": 1, "item_nb": "1001", "item_desc": "Widget",
                         "qty": "2.000", "uom": "EA", "is_free": False}],
            })

        _mock_client(handler, monkeypatch)
        result = catalog_client.create_order(
            commit_intent_id="intent-1", order_type="SO", cust_nb="58466",
            cust_nb_override=None, target_order_nb_override=None,
            primary_intent="add_order", full_return=False, lines=[],
            line_edits=[], removed_line_nbs=[], is_return=False,
            acting_salesman_id="sm1")
        assert result.order_nb == "260000123"
        assert result.lines[0].qty == Decimal("2.000")
