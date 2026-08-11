from app.schemas.extraction import Extraction, ExtractedLine
from app.services.catalogue import CatalogueService
from app.services.draft_builder import DraftBuilder
from app.services.flagger import Flagger
from app.services.item_classifier import UNABLE_TO_CLASSIFY, classify_line
from app.services.item_resolver import ItemResolver
from app.services.prior_order import PriorOrderService

CATEGORIES = ["Consumables", "Misc", "Paint", "Tools"]


# ---- classify_line (pure function) -------------------------------------

def test_tier1_uses_the_resolved_items_own_category():
    assert classify_line(matched_category="Paint", raw_text="anything",
                         known_categories=CATEGORIES) == "Paint"


def test_tier2_falls_back_to_category_name_in_raw_text():
    assert classify_line(matched_category=None,
                         raw_text="I need some tools for the garage",
                         known_categories=CATEGORIES) == "Tools"


def test_tier3_unable_to_classify_when_nothing_matches():
    assert classify_line(matched_category=None,
                         raw_text="1234567 xxxxxxx yyyyyyy zzzzzzz",
                         known_categories=CATEGORIES) == UNABLE_TO_CLASSIFY


def test_empty_raw_text_is_unable_to_classify():
    assert classify_line(matched_category=None, raw_text="",
                         known_categories=CATEGORIES) == UNABLE_TO_CLASSIFY
    assert classify_line(matched_category=None, raw_text=None,
                         known_categories=CATEGORIES) == UNABLE_TO_CLASSIFY


# ---- integration through DraftBuilder._from_extraction ------------------

def _builder(session):
    resolver = ItemResolver(session)
    return DraftBuilder(session, resolver, PriorOrderService(session),
                        Flagger(), CatalogueService(session, resolver))


def test_resolved_item_gets_its_catalogue_category(db_session):
    builder = _builder(db_session)
    extraction = Extraction(lines=[ExtractedLine(
        raw_text="blue paint", product="Blue Paint 5L", qty=2, uom="PCS")])
    lines = builder._from_extraction(extraction, "C001")
    assert lines[0].item_nb == "A100"
    assert lines[0].category == "Paint"


def test_unresolved_item_falls_back_to_category_guess(db_session):
    builder = _builder(db_session)
    extraction = Extraction(lines=[ExtractedLine(
        raw_text="need some tools for the garage", product=None, qty=1,
        uom=None)])
    lines = builder._from_extraction(extraction, "C001")
    assert lines[0].item_nb is None
    assert lines[0].category == "Tools"


def test_completely_unmatched_item_is_unable_to_classify(db_session):
    builder = _builder(db_session)
    extraction = Extraction(lines=[ExtractedLine(
        raw_text="1234567 xxxxxxx yyyyyyy zzzzzzz", product=None, qty=1,
        uom=None)])
    lines = builder._from_extraction(extraction, "C001")
    assert lines[0].item_nb is None
    assert lines[0].category == UNABLE_TO_CLASSIFY
