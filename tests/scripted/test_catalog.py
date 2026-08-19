import openpyxl
import pytest

from app.services.scripted.catalog import (CatalogLoadError,
                                            duplicate_descriptions,
                                            import_catalog, load_catalog)


def _make_xlsm(tmp_path, rows, header=("ItemFamily", "ItemNumber",
                                       "ItemDescription")):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Export"
    ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    path = tmp_path / "Product.xlsm"
    wb.save(path)
    return str(path)


def test_forward_fills_item_family(tmp_path):
    path = _make_xlsm(tmp_path, [
        ("Adult Diapers", "N1", "Item One"),
        (None, "N2", "Item Two"),
        (None, "N3", "Item Three"),
        ("Paper", "N4", "Item Four"),
        (None, "N5", "Item Five"),
    ])
    cat = load_catalog(path)
    families = {r.item_number: r.item_family for r in cat}
    assert families == {"N1": "Adult Diapers", "N2": "Adult Diapers",
                        "N3": "Adult Diapers", "N4": "Paper", "N5": "Paper"}


def test_skips_blank_and_footer_rows(tmp_path):
    path = _make_xlsm(tmp_path, [
        ("Adult Diapers", "N1", "Item One"),
        (None, None, None),
        ("No filters applied", None, None),
    ])
    cat = load_catalog(path)
    assert [r.item_number for r in cat] == ["N1"]


def test_preserves_original_description_unmodified(tmp_path):
    path = _make_xlsm(tmp_path, [
        ("Adult Diapers", "N1", "Medica Undrpad GR(80x60cm)20X4"),
    ])
    cat = load_catalog(path)
    assert cat[0].original_description == "Medica Undrpad GR(80x60cm)20X4"
    assert cat[0].normalized_description != cat[0].original_description


def test_missing_required_column_errors(tmp_path):
    path = _make_xlsm(tmp_path, [("N1", "Item One")],
                      header=("ItemNumber", "ItemDescription"))
    with pytest.raises(CatalogLoadError):
        load_catalog(path)


def test_duplicate_descriptions_detected(tmp_path):
    path = _make_xlsm(tmp_path, [
        ("Adult Diapers", "N1", "TENDREX ADULT MED 12X4"),
        (None, "N1TD", "TENDREX ADULT MED 12X4"),
        (None, "N2", "OTHER ITEM"),
    ])
    cat = load_catalog(path)
    dups = duplicate_descriptions(cat)
    assert dups == {"TENDREX ADULT MED 12X4": ["N1", "N1TD"]}


def test_import_catalog_is_idempotent(tmp_path, db_session):
    # db_session rolls back at test end (see conftest.py) - no explicit
    # cleanup needed, matching the rest of the suite's convention.
    from app.models import Item
    path = _make_xlsm(tmp_path, [
        ("Test Family", "ZZTEST1", "Zztest Item One"),
        (None, "ZZTEST2", "Zztest Item Two"),
    ])
    n1 = import_catalog(db_session, path)
    n2 = import_catalog(db_session, path)
    assert n1 == n2 == 2
    rows = db_session.query(Item).filter(
        Item.item_number.in_(["ZZTEST1", "ZZTEST2"])).all()
    assert len(rows) == 2
    assert {r.category for r in rows} == {"Test Family"}
