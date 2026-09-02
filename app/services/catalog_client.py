"""HTTP client for catalog-service (item/customer/order_header/
order_details/qra_header/qra_detail - see vendo-app/catalog-service).
Every function here used to be a local SQLAlchemy query or a call into
app.services.item_resolver/match_customer/qra_engine/prior_order; this
module is the one place that boundary is now crossed, returning the same
shapes those callers already expect so draft_builder.py/commit.py/the API
routers change as little as possible.
"""
from dataclasses import dataclass, field
from decimal import Decimal

import httpx

from app.config import settings
from app.services.scripted.models import CustomerMatch, ItemCandidate, ItemMatchResult, MatchStatus


def _client() -> httpx.Client:
    headers = {"X-API-Key": settings.catalog_api_key} if settings.catalog_api_key else {}
    return httpx.Client(base_url=settings.catalog_service_url, headers=headers,
                        timeout=settings.catalog_timeout_seconds)


def _get(path: str, **params):
    """GET `path`, raising on any non-2xx status, returning the parsed JSON
    body. `params` values of None are dropped by httpx before the request
    is sent, same as every call site already relied on."""
    with _client() as c:
        r = c.get(path, params=params)
        r.raise_for_status()
        return r.json()


def _post(path: str, json: dict):
    with _client() as c:
        r = c.post(path, json=json)
        r.raise_for_status()
        return r.json()


def _patch(path: str, json: dict):
    with _client() as c:
        r = c.patch(path, json=json)
        r.raise_for_status()
        return r.json()


# ---- items ---------------------------------------------------------------

def resolve_item(item_span: str) -> ItemMatchResult:
    body = _get("/items/resolve", q=item_span)
    return ItemMatchResult(
        item_number=body["item_number"], item_description=body["item_description"],
        item_family=body["item_family"], status=MatchStatus(body["status"]),
        score=body["score"], method=body["method"],
        explanation=body.get("explanation", ""),
        candidates=[
            ItemCandidate(item_number=c["item_number"],
                          item_description=c["item_description"],
                          item_family=c["item_family"], score=c["score"],
                          numeric_compatible=c["numeric_compatible"],
                          numeric_conflict_reason=c.get("numeric_conflict_reason"))
            for c in body.get("candidates", [])
        ])


@dataclass
class ItemCacheRow:
    item_nb: str
    item_desc: str
    category: str


def search_items(q: str) -> list[dict]:
    return _get("/items/search", q=q)


def list_all_items() -> list[ItemCacheRow]:
    return [ItemCacheRow(**row) for row in _get("/items/all")]


def all_item_categories() -> list[str]:
    return sorted({row["category"] for row in _get("/items/all")})


def get_items_batch(item_nbs: list[str]) -> dict[str, str]:
    """item_nb -> category for a specific set of numbers - backs
    draft_builder.py's "look up each item's current category" step
    without pulling the full catalogue for a handful of lookups. Returns
    {} for an empty input without a network call."""
    numbers = [n for n in dict.fromkeys(item_nbs) if n]
    if not numbers:
        return {}
    rows = _get("/items/by-numbers", nbs=",".join(numbers))
    return {row["item_nb"]: row["category"] for row in rows}


# ---- customers ------------------------------------------------------------

def match_customer(text_val: str) -> CustomerMatch:
    body = _get("/customers/match", q=text_val)
    return CustomerMatch(customer_number=body["cust_nb"],
                         customer_name=body["customer_name"],
                         score=body["score"], status=MatchStatus(body["status"]))


def search_customers(q: str, *, salesman_id: str | None = None,
                     admin: bool = False) -> list[tuple[str, str, float]]:
    """salesman_id/admin come from the caller's own authenticated identity
    (app.api.deps.get_current_salesman), never from a client-supplied
    parameter - see app/api/customers.py."""
    rows = _get("/customers/search", q=q, salesman_id=salesman_id, admin=admin)
    return [(row["cust_nb"], row["customer_name"], row["score"]) for row in rows]


@dataclass
class CustomerRow:
    cust_nb: str
    customer_name: str


def list_all_customers(*, salesman_id: str | None = None,
                       admin: bool = False) -> list[CustomerRow]:
    """salesman_id/admin: see search_customers() above - same contract."""
    rows = _get("/customers/all", salesman_id=salesman_id, admin=admin)
    return [CustomerRow(**row) for row in rows]


def get_customers_batch(cust_nbs: list[str]) -> dict[str, str]:
    """cust_nb -> customer_name for a specific set of numbers - backs list
    views (queue listing, /orders/recent) that need many customers' names
    at once without pulling the full customer table per request. Returns
    {} for an empty input without a network call."""
    numbers = [n for n in dict.fromkeys(cust_nbs) if n]
    if not numbers:
        return {}
    rows = _get("/customers/by-numbers", nbs=",".join(numbers))
    return {row["cust_nb"]: row["customer_name"] for row in rows}


def get_customer(cust_nb: str) -> CustomerRow | None:
    """Existence/name lookup for one customer - backs the places that used
    to do session.get(Customer, cust_nb) for display purposes only (commit-
    time existence validation now happens inside catalog-service's
    POST /orders, not here)."""
    name = get_customers_batch([cust_nb]).get(cust_nb)
    return CustomerRow(cust_nb=cust_nb, customer_name=name) if name else None


@dataclass
class CustomerDetail:
    cust_nb: str
    customer_name: str
    email: str | None
    telephone: str | None
    city: str | None
    address1: str | None
    salesman_id: str | None


def _customer_detail(body: dict) -> CustomerDetail:
    return CustomerDetail(
        cust_nb=body["cust_nb"], customer_name=body["customer_name"],
        email=body.get("email"), telephone=body.get("telephone"),
        city=body.get("city"), address1=body.get("address1"),
        salesman_id=body.get("salesman_id"))


def get_customer_detail(cust_nb: str) -> CustomerDetail | None:
    """Full detail for one customer, including salesman_id - backs the
    backend's direct-access endpoint (GET /customers/{cust_nb}) and the
    ownership checks on claim/reject/callback, which (unlike accept) never
    reach catalog-service's own POST /orders check."""
    body = _get(f"/customers/{cust_nb}")
    return _customer_detail(body) if body is not None else None


def assign_customer_salesman(cust_nb: str, salesman_id: str | None
                             ) -> CustomerDetail:
    """Admin-only in practice - the caller (app/api/customers.py) has
    already checked the acting salesman's role and, when salesman_id is
    set, that it names a real active Salesman row. This call itself just
    persists the assignment; catalog-service has no notion of roles."""
    return _customer_detail(
        _patch(f"/customers/{cust_nb}/salesman", json={"salesman_id": salesman_id}))


# ---- qra --------------------------------------------------------------

def list_all_qra() -> list[dict]:
    return _get("/qra/all")


@dataclass
class QraLinePreview:
    line_nb: int
    unit_price: Decimal | None
    is_free: bool
    substituted_item_nb: str | None
    substituted_item_desc: str | None


@dataclass
class QraBonusLinePreview:
    item_nb: str
    item_desc: str
    qty: Decimal
    uom: str | None


def preview_qra(cust_nb: str | None, lines,
                is_return: bool = False
                ) -> tuple[list[QraLinePreview], list[QraBonusLinePreview]]:
    """`lines` is anything with line_nb/item_nb/item_desc/category/qty/uom
    attributes (PendingLine rows in practice)."""
    if not lines:
        return [], []
    body = {
        "cust_nb": cust_nb, "is_return": is_return,
        "lines": [{"line_nb": l.line_nb, "item_nb": l.item_nb,
                  "item_desc": l.item_desc, "category": l.category,
                  "qty": str(l.qty) if l.qty is not None else None,
                  "uom": l.uom} for l in lines],
    }
    result = _post("/qra/preview", json=body)
    return (
        [QraLinePreview(line_nb=p["line_nb"],
                        unit_price=(Decimal(p["unit_price"])
                                   if p["unit_price"] is not None else None),
                        is_free=p["is_free"],
                        substituted_item_nb=p["substituted_item_nb"],
                        substituted_item_desc=p["substituted_item_desc"])
        for p in result["lines"]],
        [QraBonusLinePreview(item_nb=b["item_nb"], item_desc=b["item_desc"],
                             qty=Decimal(b["qty"]), uom=b["uom"])
        for b in result["bonus_lines"]],
    )


# ---- orders (read side) ------------------------------------------------

@dataclass
class OrderLine:
    line_nb: int
    item_nb: str | None
    item_desc: str | None
    qty: Decimal | None
    uom: str | None
    is_free: bool = False


@dataclass
class OrderRef:
    order_nb: str
    order_type: str
    cust_nb: str
    customer_name: str | None = None
    lines: list[OrderLine] = field(default_factory=list)


def _order_ref(body: dict | None) -> OrderRef | None:
    if body is None:
        return None
    return OrderRef(
        order_nb=body["order_nb"], order_type=body["order_type"],
        cust_nb=body["cust_nb"], customer_name=body.get("customer_name"),
        lines=[OrderLine(line_nb=l["line_nb"], item_nb=l["item_nb"],
                         item_desc=l["item_desc"], qty=_dec(l["qty"]),
                         uom=l["uom"], is_free=l.get("is_free", False))
              for l in body.get("lines", [])])


def _dec(v) -> Decimal | None:
    return Decimal(v) if v is not None else None


def get_recent_orders(*, salesman_id: str | None = None, admin: bool = False,
                      limit: int = 30) -> list[OrderRef]:
    """Recently committed orders for the caller's own customers (or every
    customer, for an admin) - backs GET /orders/recent. Reads straight off
    order_header/customer in catalog-service; doesn't depend on
    PendingRequest still existing (a committed request's buffer row is
    deleted once its order exists - see OrderCommitService._finalize_committed).
    salesman_id/admin: same trusted-caller contract as search_customers().
    """
    rows = _get("/orders/recent", salesman_id=salesman_id, admin=admin, limit=limit)
    return [_order_ref(row) for row in rows]


def get_order(order_nb: str, order_type: str) -> OrderRef | None:
    return _order_ref(_get(f"/orders/{order_nb}/{order_type}"))


def find_so_by_order_nb(ref: str | None) -> OrderRef | None:
    if not ref:
        return None
    return _order_ref(_get(f"/orders/by-so-nb/{ref}"))


def resolve_target(cust_nb: str, reference: str | None
                   ) -> tuple[OrderRef | None, str | None]:
    body = _post("/orders/resolve-target",
                 json={"cust_nb": cust_nb, "mode": "implicit", "reference": reference})
    if body.get("order_nb") is None:
        return None, body.get("ambiguity")
    return _order_ref(body), None


def resolve_target_explicit(cust_nb: str, mode: str, value: str | None
                            ) -> tuple[OrderRef | None, str | None]:
    if mode != "order_nb":
        return None, "unknown_mode"
    body = _post("/orders/resolve-target",
                 json={"cust_nb": cust_nb, "mode": "explicit", "reference": value})
    if body.get("order_nb") is None:
        return None, body.get("ambiguity")
    return _order_ref(body), None


# ---- orders (commit side) ----------------------------------------------

class CommitTransientError(Exception):
    """catalog-service was unreachable, timed out, or errored in a way
    that isn't a definitive validation failure - the caller should leave
    the PendingRequest in "committing" for the reconciliation sweep
    (app/worker.py) rather than reverting it, since the order may or may
    not have actually been created."""


@dataclass
class LineIn:
    line_nb: int
    item_nb: str | None
    item_desc: str | None
    qty: Decimal | None
    uom: str | None


@dataclass
class LineEditIn:
    line_nb: int
    item_nb: str | None = None
    item_desc: str | None = None
    qty: Decimal | None = None
    uom: str | None = None


@dataclass
class CreateOrderResult:
    order_nb: str
    order_type: str
    cust_nb: str
    target_order_nb: str | None
    target_order_type: str | None
    lines: list[OrderLine]


def create_order(*, commit_intent_id: str, order_type: str,
                 cust_nb: str | None, cust_nb_override: str | None,
                 target_order_nb_override: str | None,
                 primary_intent: str | None, full_return: bool,
                 lines: list[LineIn], line_edits: list[LineEditIn],
                 removed_line_nbs: list[int], is_return: bool,
                 acting_salesman_id: str, acting_is_admin: bool = False
                 ) -> CreateOrderResult:
    """Raises app.errors.{CustomerNotFound,CustomerNotAuthorized,
    TargetOrderNotFound,OrderAlreadyReturned,UnresolvedLines} on a
    definitive validation failure (catalog-service responded 422 with a
    typed error code), or CommitTransientError for anything else (network
    failure, timeout, unexpected 5xx) - see OrderCommitService.commit() for
    how each is handled.

    acting_salesman_id/acting_is_admin identify the salesman this backend
    has already authenticated (app.api.deps.get_current_salesman) - never
    anything client-supplied. catalog-service's own POST /orders uses these
    to enforce that a non-admin may only place an order for a customer
    assigned to them, checked there (not here) because it's the only place
    the final, fully-resolved cust_nb is known - a RETURN or reorder-by-
    order-number can resolve to a different customer than the one named in
    the request.

    Deliberately not routed through _post()/_get() above: those raise on
    any non-2xx status, but a 422 here is an expected, typed outcome this
    function must inspect and translate itself, not a transport failure.
    """
    from app.errors import (CustomerNotAuthorized, CustomerNotFound,
                            OrderAlreadyReturned, TargetOrderNotFound,
                            UnresolvedLines)

    body = {
        "commit_intent_id": commit_intent_id, "order_type": order_type,
        "cust_nb": cust_nb, "cust_nb_override": cust_nb_override,
        "target_order_nb_override": target_order_nb_override,
        "primary_intent": primary_intent, "full_return": full_return,
        "lines": [{"line_nb": l.line_nb, "item_nb": l.item_nb,
                  "item_desc": l.item_desc,
                  "qty": str(l.qty) if l.qty is not None else None,
                  "uom": l.uom} for l in lines],
        "line_edits": [{"line_nb": e.line_nb, "item_nb": e.item_nb,
                       "item_desc": e.item_desc,
                       "qty": str(e.qty) if e.qty is not None else None,
                       "uom": e.uom} for e in line_edits],
        "removed_line_nbs": removed_line_nbs, "is_return": is_return,
        "acting_salesman_id": acting_salesman_id,
        "acting_is_admin": acting_is_admin,
    }
    try:
        with _client() as c:
            r = c.post("/orders", json=body)
    except httpx.HTTPError as e:
        raise CommitTransientError(str(e)) from e

    if r.status_code == 422:
        err = r.json().get("detail", {})
        code = err.get("code")
        detail = err.get("detail", "")
        if code == "customer_not_found":
            raise CustomerNotFound(cust_nb)
        if code == "customer_not_authorized":
            raise CustomerNotAuthorized(cust_nb_override or cust_nb)
        if code == "target_order_not_found":
            raise TargetOrderNotFound(target_order_nb_override)
        if code == "order_already_returned":
            raise OrderAlreadyReturned(target_order_nb_override or "")
        if code == "unresolved_lines":
            raise UnresolvedLines()
        raise CommitTransientError(f"unrecognized catalog-service error: {detail}")
    if r.status_code >= 400:
        raise CommitTransientError(f"catalog-service returned {r.status_code}: {r.text}")

    result = r.json()
    return CreateOrderResult(
        order_nb=result["order_nb"], order_type=result["order_type"],
        cust_nb=result["cust_nb"], target_order_nb=result["target_order_nb"],
        target_order_type=result["target_order_type"],
        lines=[OrderLine(line_nb=l["line_nb"], item_nb=l["item_nb"],
                         item_desc=l["item_desc"], qty=_dec(l["qty"]),
                         uom=l["uom"], is_free=l.get("is_free", False))
              for l in result["lines"]])
