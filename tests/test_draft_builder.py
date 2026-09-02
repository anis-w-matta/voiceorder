from app.services.draft_builder import DraftBuilder
from app.services.scripted.models import (ItemCandidate, ItemMatchResult,
                                          MatchStatus, QuantityUOM,
                                          ResolvedOrderLine,
                                          ScriptedOrderResult)


class _FakeCatalogue:
    def all_categories(self):
        return []


def _scripted_result_with_conflicting_candidate():
    candidate = ItemCandidate(
        item_number="X1", item_description="Widget 20x4", item_family="hw",
        score=88.0, numeric_compatible=False,
        numeric_conflict_reason="size 20x4 != requested 12x4")
    match = ItemMatchResult(
        item_number=None, item_description=None, item_family=None,
        status=MatchStatus.AMBIGUOUS, score=88.0, method="fuzzy",
        candidates=[candidate])
    qty = QuantityUOM(quantity=None, uom=None, raw_text="some widgets",
                      status="error", reason="unparseable")
    line = ResolvedOrderLine(raw_item_text="some widgets", qty=qty, match=match)
    return ScriptedOrderResult(status="needs_confirmation",
                               command_type="place_order", lines=[line])


def test_pending_lines_from_scripted_carries_conflict_reason():
    """A candidate's numeric_conflict_reason (why it didn't cleanly match -
    e.g. a pack-size mismatch) must reach the PendingLine.candidates dict the
    same way attribute_conflict does, so the reviewer sees *why*, not just
    that a conflict exists."""
    builder = DraftBuilder(session=None, prior=None, catalogue=_FakeCatalogue())
    scripted = _scripted_result_with_conflicting_candidate()

    lines = builder._pending_lines_from_scripted(scripted)

    assert len(lines) == 1
    [candidate] = lines[0].candidates
    assert candidate["attribute_conflict"] is True
    assert candidate["conflict_reason"] == "size 20x4 != requested 12x4"
