from sqlalchemy import select

from app.models import ActivityLog, Item, ItemAlias
from app.services.alias_learning import maybe_learn_alias


def _seed_items(session, *nbs):
    session.add_all([Item(item_number=nb, item_desc=f"Widget {nb}",
                          category="Misc") for nb in nbs])
    session.flush()


def test_correction_creates_human_correction_alias(db_session):
    _seed_items(db_session, "Z930", "Z931")
    alias = maybe_learn_alias(db_session, raw_text="cleaning sponge",
                              item_nb="Z930", suggested_item_nb="Z931",
                              remember=True)
    assert alias is not None
    assert alias.source == "human_correction"
    assert alias.confidence == 1.0
    assert alias.normalized_alias == "cleaning sponge"
    assert alias.item_number == "Z930"


def test_remember_false_does_not_write_anything(db_session):
    _seed_items(db_session, "Z932", "Z933")
    alias = maybe_learn_alias(db_session, raw_text="cleaning sponge",
                              item_nb="Z932", suggested_item_nb="Z933",
                              remember=False)
    assert alias is None


def test_correction_matching_the_suggestion_teaches_nothing_new(db_session):
    _seed_items(db_session, "Z934")
    alias = maybe_learn_alias(db_session, raw_text="cleaning sponge",
                              item_nb="Z934", suggested_item_nb="Z934",
                              remember=True)
    assert alias is None


def test_resubmitting_same_correction_is_idempotent(db_session):
    _seed_items(db_session, "Z935", "Z936")
    maybe_learn_alias(db_session, raw_text="cleaning sponge", item_nb="Z935",
                      suggested_item_nb="Z936", remember=True)
    before = len(db_session.scalars(select(ItemAlias).where(
        ItemAlias.item_number == "Z935")).all())
    again = maybe_learn_alias(db_session, raw_text="cleaning sponge",
                              item_nb="Z935", suggested_item_nb="Z936",
                              remember=True)
    after = len(db_session.scalars(select(ItemAlias).where(
        ItemAlias.item_number == "Z935")).all())
    assert again is None
    assert before == after == 1


def test_correction_conflicting_with_existing_alias_does_not_overwrite(db_session):
    _seed_items(db_session, "Z937", "Z938")
    db_session.add(ItemAlias(item_number="Z937", alias="cleaning sponge",
                             lang="en", source="seed"))
    db_session.flush()

    alias = maybe_learn_alias(db_session, raw_text="cleaning sponge",
                              item_nb="Z938", suggested_item_nb=None,
                              remember=True)
    assert alias is None
    rows = db_session.scalars(select(ItemAlias).where(
        ItemAlias.normalized_alias == "cleaning sponge")).all()
    assert len(rows) == 1
    assert rows[0].item_number == "Z937"

    # log_standalone commits in its own transaction, independent of this
    # test's rollback-based db_session, so rows from other test runs may
    # already exist - assert presence of a matching row, not an exact
    # count, to stay stable across repeated runs.
    log_rows = db_session.scalars(select(ActivityLog).where(
        ActivityLog.event_type == "alias_learning_conflict")).all()
    assert any(r.details.get("item_nb") == "Z938" and
              r.details.get("raw_text") == "cleaning sponge"
              for r in log_rows)
