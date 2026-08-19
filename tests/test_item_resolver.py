from app.models import Item, ItemAlias
from app.services.item_resolver import ItemResolver


def _seed_tendrex(session):
    """Mirrors the real bug that motivated TIE_EPSILON: two variants of the
    same product, distinguished only by a size word in item_desc, that
    fuzzy-match the same query with an (almost) identical score."""
    session.add_all([
        Item(item_number="Z901", item_desc="Tendrex Eco Sponge MED",
             category="Misc"),
        Item(item_number="Z902", item_desc="Tendrex Eco Sponge LRG",
             category="Misc"),
    ])
    session.flush()


# ---- attributes=None must behave exactly as before -----------------------

def test_resolve_without_attributes_behaves_as_before(db_session):
    session = db_session
    session.add(Item(item_number="Z800", item_desc="Plain Widget",
                     category="Misc"))
    session.flush()
    resolver = ItemResolver(session)
    match, cands = resolver.resolve("Z800")
    assert match is not None
    assert match.item_nb == "Z800"
    assert match.score == 1.0
    assert match.attribute_conflict is False


# ---- engineered tie: two equally-good candidates, no size stated ---------

def test_engineered_tie_leaves_top_none_and_flags_ambiguous(db_session):
    _seed_tendrex(db_session)
    resolver = ItemResolver(db_session)
    match, cands = resolver.resolve("tendrex eco sponge")
    assert match is None
    assert len(cands) == 2
    assert abs(cands[0].score - cands[1].score) <= 0.02

    from app.services.item_resolver import tied_with_top
    tied = tied_with_top(cands)
    assert len(tied) == 2  # both candidates are a genuine coin-flip tie


# ---- attribute conflict penalizes the wrong-size variant ------------------

def test_attribute_conflict_penalizes_wrong_size_and_resolves_the_right_one(db_session):
    _seed_tendrex(db_session)
    resolver = ItemResolver(db_session)
    match, cands = resolver.resolve("tendrex eco sponge",
                                    attributes={"size": "large"})
    assert match is not None
    assert match.item_nb == "Z902"  # the LRG variant

    med = next(c for c in cands if c.item_nb == "Z901")
    lrg = next(c for c in cands if c.item_nb == "Z902")
    assert med.attribute_conflict is True
    assert "MED" in med.conflict_reason
    assert med.score < lrg.score  # penalty pushed the conflicting one below the clean one
    assert lrg.score - med.score >= 0.35 - 1e-9  # the configured penalty was applied


def test_attribute_conflict_penalizes_wrong_color(db_session):
    db_session.add_all([
        Item(item_number="Z910", item_desc="Widget Basic RED", category="Misc"),
        Item(item_number="Z911", item_desc="Widget Basic BLUE", category="Misc"),
    ])
    db_session.flush()
    resolver = ItemResolver(db_session)
    match, cands = resolver.resolve("widget basic",
                                    attributes={"color": "blue"})
    assert match is not None
    assert match.item_nb == "Z911"


# ---- an exact match is not auto-accepted if it conflicts -----------------

def test_exact_alias_match_with_conflicting_attribute_is_not_auto_accepted(db_session):
    db_session.add(Item(item_number="Z920", item_desc="Sample Sponge MED",
                        category="Misc"))
    db_session.flush()
    resolver = ItemResolver(db_session)

    # Exact PK lookup would normally auto-accept at score=1.0.
    match, cands = resolver.resolve("Z920", attributes={"size": "large"})
    assert match is None
    assert len(cands) == 1
    assert cands[0].attribute_conflict is True


def test_exact_alias_match_without_conflict_is_still_auto_accepted(db_session):
    db_session.add(Item(item_number="Z921", item_desc="Sample Sponge LRG",
                        category="Misc"))
    db_session.flush()
    resolver = ItemResolver(db_session)
    match, cands = resolver.resolve("Z921", attributes={"size": "large"})
    assert match is not None
    assert match.item_nb == "Z921"


# ---- alias bridges a vocabulary gap between spoken and catalogue terms ---

def test_alias_bridges_vocabulary_gap_cleaning_vs_cellulosic_sponge(db_session):
    db_session.add(Item(item_number="Z900", item_desc="Cellulosic Sponge",
                        category="Cleaning"))
    db_session.flush()
    db_session.add(ItemAlias(item_number="Z900", alias="cleaning sponge",
                             lang="en"))
    db_session.flush()

    resolver = ItemResolver(db_session)
    match, cands = resolver.resolve("cleaning sponge")
    assert match is not None
    assert match.item_nb == "Z900"
    assert match.method == "alias"


# ---- QA regression: exact-match duplicates must never auto-resolve -------
# (found via the adversarial stress-test suite - a real catalogue had a
# TD-suffix duplicate SKU sharing an identical item_desc, and the
# exact-desc-match branch used SELECT...LIMIT 1 with no ORDER BY, silently
# picking whichever row Postgres happened to return first at 0.98
# confidence with zero review flag.)

def test_duplicate_exact_description_requires_review_not_arbitrary_pick(db_session):
    db_session.add_all([
        Item(item_number="Z950", item_desc="Tendrex Adult Large 12X4",
             category="Misc"),
        Item(item_number="Z950TD", item_desc="Tendrex Adult Large 12X4",
             category="Misc"),
    ])
    db_session.flush()
    resolver = ItemResolver(db_session)
    match, cands = resolver.resolve("Tendrex Adult Large 12X4")
    assert match is None
    assert {c.item_nb for c in cands} == {"Z950", "Z950TD"}


def test_duplicate_alias_normalizing_to_same_text_requires_review(db_session):
    db_session.add_all([
        Item(item_number="Z951", item_desc="Widget One", category="Misc"),
        Item(item_number="Z952", item_desc="Widget Two", category="Misc"),
    ])
    db_session.flush()
    db_session.add_all([
        ItemAlias(item_number="Z951", alias="the widget", lang="en"),
        ItemAlias(item_number="Z952", alias="THE WIDGET", lang="en"),
    ])
    db_session.flush()
    resolver = ItemResolver(db_session)
    match, cands = resolver.resolve("the widget")
    assert match is None
    assert {c.item_nb for c in cands} == {"Z951", "Z952"}


def test_same_item_multiple_aliases_matching_is_not_a_false_tie(db_session):
    # Two ALIAS ROWS for the SAME item normalizing to the same query text
    # must not be mistaken for a tie between different items.
    db_session.add(Item(item_number="Z953", item_desc="Widget Three", category="Misc"))
    db_session.flush()
    db_session.add_all([
        ItemAlias(item_number="Z953", alias="widget three", lang="en"),
        ItemAlias(item_number="Z953", alias="Widget Three", lang="en"),
    ])
    db_session.flush()
    resolver = ItemResolver(db_session)
    match, cands = resolver.resolve("widget three")
    assert match is not None
    assert match.item_nb == "Z953"


# ---- QA regression: a stated discount disambiguates between promo SKUs ---
# (found via the adversarial stress-test suite - qualifiers.discount_percent
# never influenced item resolution at all, so a customer explicitly stating
# 20% could silently receive the 40%-labeled SKU purely on text similarity.)

def test_stated_discount_percent_disambiguates_promo_labeled_variants(db_session):
    db_session.add_all([
        Item(item_number="Z960", item_desc="Elegance Med 12X4 SV20%",
             category="Adult Diapers"),
        Item(item_number="Z961", item_desc="Elegance Med 12X4 DIS40%",
             category="Adult Diapers"),
    ])
    db_session.flush()
    resolver = ItemResolver(db_session)
    match, cands = resolver.resolve(
        "elegance med 12x4 diapers order", qualifiers={"discount_percent": 20})
    assert match is not None
    assert match.item_nb == "Z960"
    # The 40%-labeled variant may score low enough after the promotion-
    # conflict penalty to drop out of the candidate list entirely - either
    # way it must never win, and if present must show the conflict.
    wrong = next((c for c in cands if c.item_nb == "Z961"), None)
    if wrong is not None:
        assert wrong.attribute_conflict is True


def test_exact_match_with_conflicting_promotion_is_not_auto_accepted(db_session):
    db_session.add(Item(item_number="Z962", item_desc="Elegance Med 12X4 DIS40%",
                        category="Adult Diapers"))
    db_session.flush()
    resolver = ItemResolver(db_session)
    match, cands = resolver.resolve(
        "Elegance Med 12X4 DIS40%", qualifiers={"discount_percent": 20})
    assert match is None
    assert cands[0].attribute_conflict is True


# ---- QA regression: punctuation in the query must not degrade fuzzy score
# (found via the adversarial stress-test suite's fuzz-variation cases -
# transcripts routinely carry commas/hyphens the fuzzy stage sent straight
# into pg_trgm/rapidfuzz unnormalized, measurably weakening otherwise-clear
# matches.)

def test_punctuation_in_query_does_not_degrade_fuzzy_match(db_session):
    db_session.add(Item(item_number="Z970", item_desc="Sanita Widget Large",
                        category="Misc"))
    db_session.flush()
    resolver = ItemResolver(db_session)
    clean, _ = resolver.resolve("sanita widget large")
    punctuated, _ = resolver.resolve("sanita, widget, large!")
    assert clean is not None
    assert punctuated is not None
    assert punctuated.item_nb == clean.item_nb == "Z970"


# ---- QA regression: size/color detection on a glued pack-count suffix ----
# (found while seeding realistic demo data against the real catalogue -
# "MEDICA ADULT DIAPER MED20X4" has no space between the size abbreviation
# and the trailing pack-count digits, so the old alnum-boundary check never
# found "MED" there at all, silently weakening the attribute-conflict
# safety check for exactly this real, common formatting pattern.)

def test_desc_size_detected_even_when_glued_to_a_trailing_digit(db_session):
    db_session.add_all([
        Item(item_number="Z980", item_desc="Medica Adult Diaper MED20X4",
             category="Adult Diapers"),
        Item(item_number="Z981", item_desc="Medica Adult Diaper LRG 20X4",
             category="Adult Diapers"),
    ])
    db_session.flush()
    resolver = ItemResolver(db_session)
    match, cands = resolver.resolve("Medica Adult Diaper", attributes={"size": "large"})
    med = next(c for c in cands if c.item_nb == "Z980")
    lrg = next(c for c in cands if c.item_nb == "Z981")
    assert med.attribute_conflict is True
    assert lrg.attribute_conflict is False


def test_desc_size_still_does_not_false_positive_inside_a_brand_name(db_session):
    # "MED" must not be detected inside "MEDICA" itself - only a real,
    # letter-bounded size token counts.
    db_session.add(Item(item_number="Z982", item_desc="Medica Adult Diaper LRG 20X4",
                        category="Adult Diapers"))
    db_session.flush()
    resolver = ItemResolver(db_session)
    match, cands = resolver.resolve("Medica Adult Diaper", attributes={"size": "large"})
    lrg = next(c for c in cands if c.item_nb == "Z982")
    assert lrg.attribute_conflict is False
