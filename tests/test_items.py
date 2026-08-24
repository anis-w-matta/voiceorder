from fastapi.testclient import TestClient

from app.db import session_scope
from app.main import app
from app.models import Item, Salesman
from app.services.auth import create_token, hash_password

client = TestClient(app)


def _seed_item(item_number, item_desc, category="Misc"):
    with session_scope() as s:
        s.add(Item(item_number=item_number, item_desc=item_desc, category=category))


def _delete_item(item_number):
    with session_scope() as s:
        item = s.get(Item, item_number)
        if item is not None:
            s.delete(item)


def _ensure_salesman(login_id):
    with session_scope() as s:
        sm = s.get(Salesman, login_id)
        if sm is None:
            s.add(Salesman(login_id=login_id,
                           password_hash=hash_password("testpass123"),
                           name=login_id.title(), email=f"{login_id}@example.com"))
        else:
            sm.is_active = True


_ensure_salesman("itemsearch")
TOKEN = create_token("itemsearch")


def test_search_items_returns_ranked_candidates():
    _seed_item("ZS001", "Zigzag Sponge Medium")
    try:
        resp = client.get("/items/search?q=zigzag sponge",
                          headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200
        body = resp.json()
        assert any(c["item_nb"] == "ZS001" for c in body)
    finally:
        _delete_item("ZS001")


def test_search_items_requires_auth():
    resp = client.get("/items/search?q=anything")
    assert resp.status_code == 401


def test_search_items_no_match_returns_empty_list():
    resp = client.get("/items/search?q=zzz-nonexistent-query-zzz",
                      headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert resp.json() == []
