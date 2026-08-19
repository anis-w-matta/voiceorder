import pytest

from app.models import Item, ItemAlias
from app.services.scripted.match_item import resolve_item
from app.services.scripted.models import MatchStatus


@pytest.fixture
def catalog(db_session):
    db_session.add_all([
        Item(item_number="ZZI1", item_desc="TENDREX ADULT MED 12X4",
            category="Adult Diapers"),
        Item(item_number="ZZI2", item_desc="TENDREX ADULT MED 20X4",
            category="Adult Diapers"),
        # Genuine duplicate description, different item numbers - the
        # exact scenario spec section 26 requires AMBIGUOUS for.
        Item(item_number="ZZI3", item_desc="MEGA WIDGET LRG 5X2",
            category="Misc"),
        Item(item_number="ZZI3TD", item_desc="MEGA WIDGET LRG 5X2",
            category="Misc"),
        Item(item_number="ZZI4", item_desc="ZZBRAND SML DIAPER",
            category="Misc"),
    ])
    db_session.add(ItemAlias(item_number="ZZI4", alias="zzbrand small nappy",
                             lang="en"))
    db_session.flush()


def test_exact_normalized_match(db_session, catalog):
    r = resolve_item(db_session, "TENDREX ADULT MED 12X4")
    assert r.status == MatchStatus.MATCHED
    assert r.item_number == "ZZI1"
    assert r.method == "exact"


def test_abbreviation_match(db_session, catalog):
    # "medium" (spoken) must reach an item whose description only says
    # the abbreviated "MED".
    r = resolve_item(db_session, "tendrex adult medium 12x4")
    assert r.status == MatchStatus.MATCHED
    assert r.item_number == "ZZI1"


def test_numeric_pack_match_is_required(db_session, catalog):
    r = resolve_item(db_session, "tendrex adult med 20x4")
    assert r.status == MatchStatus.MATCHED
    assert r.item_number == "ZZI2"


def test_numeric_conflict_penalized_not_ignored(db_session, catalog):
    # Spoken pack code doesn't match either candidate's pack code exactly,
    # but is very close in text to ZZI1 - the numeric check must stop a
    # same-ish-text candidate from winning purely on fuzzy score.
    r = resolve_item(db_session, "tendrex adult med 99x9")
    for c in r.candidates:
        if c.item_number in ("ZZI1", "ZZI2"):
            assert c.numeric_compatible is False


def test_duplicate_description_is_ambiguous(db_session, catalog):
    r = resolve_item(db_session, "MEGA WIDGET LRG 5X2")
    assert r.status == MatchStatus.AMBIGUOUS
    assert r.item_number is None


def test_alias_match(db_session, catalog):
    r = resolve_item(db_session, "zzbrand small nappy")
    assert r.status == MatchStatus.MATCHED
    assert r.item_number == "ZZI4"


def test_low_fuzzy_score_is_not_found_or_ambiguous(db_session, catalog):
    r = resolve_item(db_session, "completely unrelated grocery item xyz")
    assert r.status in (MatchStatus.NOT_FOUND, MatchStatus.AMBIGUOUS)
    assert r.item_number is None


def test_duplicate_description_never_silently_resolved(db_session, catalog):
    # No LLM fallback exists (fully deterministic pipeline) - a duplicate
    # description with no distinguishing signal must stay AMBIGUOUS, never
    # arbitrarily pick one of the two identical candidates.
    r = resolve_item(db_session, "MEGA WIDGET LRG 5X2")
    assert r.status == MatchStatus.AMBIGUOUS
    assert r.item_number is None


def test_empty_item_text_not_found(db_session, catalog):
    r = resolve_item(db_session, "")
    assert r.status == MatchStatus.NOT_FOUND
