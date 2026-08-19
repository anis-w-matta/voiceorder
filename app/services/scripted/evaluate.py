"""Hand-labeled evaluation harness for the scripted-command pipeline (spec
sections 31-34).

Runnable directly against a live catalog/customer DB (e.g. the real
imported Product.xlsm data) by calling `evaluate(session, CASES)`, or
self-contained via `run_self_contained()`, which seeds a small synthetic
catalog covering every required category (spec section 32 A-J) - including
duplicate descriptions and near-confusable items - so the suite (and CI)
can run without the real catalog file present.

This intentionally measures the whole pipeline end-to-end (parser +
customer + item + qty/uom), not just item matching, because a parser
regression that misdraws a boundary is just as much a real-world failure
as a bad item match.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Customer, Item, ItemAlias
from app.services.scripted.command_parser import parse
from app.services.scripted.models import MatchStatus, ParseFailure
from app.services.scripted.resolve_order import resolve


@dataclass
class ExpectedItem:
    item_number: str
    quantity: str  # Decimal-parseable string
    uom: str


@dataclass
class EvaluationCase:
    category: str  # one of "A".."J" per spec section 32
    transcript: str
    expected_customer: str | None = None
    expected_items: list[ExpectedItem] = field(default_factory=list)
    expected_status: str = "success"  # success | needs_confirmation | parse_error
    note: str = ""


@dataclass
class CaseResult:
    case: EvaluationCase
    parser_ok: bool
    customer_ok: bool
    item_ok: bool  # every expected item matched, in order
    status_ok: bool
    detail: str = ""


def _seed_evaluation_catalog(session: Session) -> None:
    """A small synthetic catalog + customer list covering every required
    evaluation category, prefixed EVL to avoid colliding with real data."""
    session.add(Customer(customer_number="EVLC1", customer_name="Evalco Trading"))
    session.add_all([
        Item(item_number="EVLI1", item_desc="TENDREX ADULT MED 12X4",
            category="Adult Diapers"),
        Item(item_number="EVLI2", item_desc="TENDREX ADULT MED ECO 20X4",
            category="Adult Diapers"),
        # Duplicate description (category I) - two SKUs, same text.
        Item(item_number="EVLI3", item_desc="TENDREX ADULT LRG 12X4",
            category="Adult Diapers"),
        Item(item_number="EVLI3TD", item_desc="TENDREX ADULT LRG 12X4",
            category="Adult Diapers"),
        # Near-confusable (category J): same family/name, different size
        # and pack count.
        Item(item_number="EVLI4", item_desc="MEDICA PULL UPS MEDIUM14X6",
            category="Adult Diapers"),
        Item(item_number="EVLI5", item_desc="MEDICA PULL UPS LARGE 14X6",
            category="Adult Diapers"),
    ])
    session.add(ItemAlias(item_number="EVLI1", alias="tendrex kbeer 12x4",
                          lang="ar-latn"))
    session.flush()


CASES: list[EvaluationCase] = [
    # A: pure English
    EvaluationCase(
        "A",
        "place order for Evalco Trading items one tendrex adult medium "
        "12x4 quantity five cartons the end",
        expected_customer="EVLC1",
        expected_items=[ExpectedItem("EVLI1", "5", "CTN")]),
    # C: Arabizi (size word)
    EvaluationCase(
        "C",
        "place order for Evalco Trading items one tendrex adult kbeer "
        "12x4 quantity khamse cartons the end",
        expected_customer="EVLC1",
        expected_status="needs_confirmation",
        note="LRG 12X4 is a duplicate description - must stay ambiguous "
            "without a distinguishing signal, not silently resolved. No "
            "expected_items: a specific match would be a guess."),
    # D: mixed code-switching (French uom + English item)
    EvaluationCase(
        "D",
        "place order for Evalco Trading items one tendrex adult medium "
        "eco 20x4 quantity trois caisses the end",
        expected_customer="EVLC1",
        expected_items=[ExpectedItem("EVLI2", "3", "CTN")]),
    # E: abbreviation (MED already in catalogue text)
    EvaluationCase(
        "E",
        "place order for Evalco Trading items one tendrex adult med "
        "12x4 quantity two cartons the end",
        expected_customer="EVLC1",
        expected_items=[ExpectedItem("EVLI1", "2", "CTN")]),
    # F: numeric pack code precision
    EvaluationCase(
        "F",
        "place order for Evalco Trading items one tendrex adult medium "
        "eco 20x4 quantity one carton the end",
        expected_customer="EVLC1",
        expected_items=[ExpectedItem("EVLI2", "1", "CTN")]),
    # G: ASR noise in the command start
    EvaluationCase(
        "G",
        "place order fer Evalco Trading items one tendrex adult med "
        "12x4 quantity five cartons the end",
        expected_customer="EVLC1",
        expected_items=[ExpectedItem("EVLI1", "5", "CTN")]),
    # H: multiple items (2)
    EvaluationCase(
        "H",
        "place order for Evalco Trading items one tendrex adult med "
        "12x4 quantity five cartons two medica pull ups large 14x6 "
        "quantity three cartons the end",
        expected_customer="EVLC1",
        expected_items=[ExpectedItem("EVLI1", "5", "CTN"),
                       ExpectedItem("EVLI5", "3", "CTN")]),
    # I: duplicate description with no distinguishing info -> ambiguous
    EvaluationCase(
        "I",
        "place order for Evalco Trading items one tendrex adult large "
        "12x4 quantity four cartons the end",
        expected_customer="EVLC1",
        expected_status="needs_confirmation",
        note="TENDREX ADULT LRG 12X4 exists under two item numbers."),
    # J: near-confusable (same family, different size)
    EvaluationCase(
        "J",
        "place order for Evalco Trading items one medica pull ups "
        "medium 14x6 quantity six cartons the end",
        expected_customer="EVLC1",
        expected_items=[ExpectedItem("EVLI4", "6", "CTN")]),
]


def _run_case(session: Session, case: EvaluationCase) -> CaseResult:
    parsed = parse(case.transcript)
    parser_ok = not isinstance(parsed, ParseFailure)
    if not parser_ok:
        status_ok = case.expected_status == "parse_error"
        return CaseResult(case, False, False, False, status_ok,
                          detail=f"parse failed: {parsed.error}")

    result = resolve(session, parsed)
    status_ok = result.status == case.expected_status

    customer_ok = (case.expected_customer is None or
                  (result.customer is not None and
                   result.customer.customer_number == case.expected_customer))

    item_ok = True
    if case.expected_items:
        if len(result.lines) != len(case.expected_items):
            item_ok = False
        else:
            for line, expected in zip(result.lines, case.expected_items):
                if (line.match.status != MatchStatus.MATCHED or
                   line.match.item_number != expected.item_number or
                   line.qty.quantity != Decimal(expected.quantity) or
                   line.qty.uom != expected.uom):
                    item_ok = False
                    break
    elif case.expected_status == "success":
        item_ok = len(result.lines) == 0

    return CaseResult(case, True, customer_ok, item_ok, status_ok)


def evaluate(session: Session, cases: list[EvaluationCase]) -> dict:
    """Run every case, return per-category and aggregate metrics (spec
    section 33): parser success rate, customer top-1 accuracy, item
    top-1/ambiguity-detection accuracy, overall pass rate."""
    results = [_run_case(session, c) for c in cases]
    n = len(results) or 1

    def rate(pred) -> float:
        return round(sum(1 for r in results if pred(r)) / n, 3)

    by_category: dict[str, list[CaseResult]] = {}
    for r in results:
        by_category.setdefault(r.case.category, []).append(r)

    return {
        "n_cases": len(results),
        "parser_success_rate": rate(lambda r: r.parser_ok),
        "customer_accuracy": rate(lambda r: r.customer_ok),
        "item_accuracy": rate(lambda r: r.item_ok),
        "overall_pass_rate": rate(
            lambda r: r.parser_ok and r.customer_ok and r.item_ok and r.status_ok),
        "by_category": {
            cat: round(sum(1 for r in rs if r.parser_ok and r.customer_ok
                          and r.item_ok and r.status_ok) / len(rs), 3)
            for cat, rs in by_category.items()},
        "failures": [
            {"category": r.case.category, "transcript": r.case.transcript,
             "detail": r.detail, "note": r.case.note}
            for r in results
            if not (r.parser_ok and r.customer_ok and r.item_ok and r.status_ok)],
    }


def sweep_thresholds(session: Session, cases: list[EvaluationCase],
                     thresholds: list[float] = (65, 70, 75, 80, 85, 90)
                     ) -> dict[float, dict]:
    """Spec section 34: measure item-match accuracy at each candidate
    ITEM_FUZZY_THRESHOLD rather than assuming 75 is correct. Operates at
    the match_item.py level directly (bypassing customer/qty resolution,
    which this threshold doesn't affect), passing `fuzzy_threshold`
    explicitly per call rather than mutating shared config state.
    """
    from app.services.scripted.match_item import resolve_item

    out: dict[float, dict] = {}
    for t in thresholds:
        total = correct = false_positive = false_negative = 0
        for case in cases:
            for expected in case.expected_items:
                total += 1
                parsed = parse(case.transcript)
                if isinstance(parsed, ParseFailure):
                    continue
                item_texts = [i.item_text for i in getattr(parsed, "items", [])]
                if not item_texts:
                    continue
                # Evaluate every spoken item span at this threshold; count
                # a hit if any resolves to the expected item_number.
                hit = False
                for item_text in item_texts:
                    r = resolve_item(session, item_text, fuzzy_threshold=t)
                    if r.item_number == expected.item_number:
                        hit = True
                    elif r.status == MatchStatus.MATCHED:
                        false_positive += 1
                if hit:
                    correct += 1
                elif case.expected_status == "success":
                    false_negative += 1
        out[t] = {
            "item_accuracy": round(correct / total, 3) if total else None,
            "false_positives": false_positive,
            "false_negatives": false_negative,
        }
    return out


def run_self_contained() -> dict:
    """Entry point for `python -m app.services.scripted.evaluate` - seeds
    the synthetic catalog above into the test schema and runs CASES,
    printing the report. Never touches dev/prod data (uses the same
    voiceorder_test schema convention as the rest of the suite)."""
    import os
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://voiceorder:changeme@localhost/voiceorder"
        "?options=-c%20search_path%3Dvoiceorder_test%2Cpublic")
    from app.db import SessionLocal
    s = SessionLocal()
    try:
        _seed_evaluation_catalog(s)
        report = evaluate(s, CASES)
    finally:
        s.rollback()  # evaluation data is never persisted
        s.close()
    return report


if __name__ == "__main__":
    import json
    print(json.dumps(run_self_contained(), indent=2))
