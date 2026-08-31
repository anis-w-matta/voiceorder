"""app/services/authorization.py - the ownership gate shared by the
queue/review endpoints. The real database-backed check for the accept/
commit path lives in catalog-service's POST /orders (see commit.py's
call into catalog_client.create_order); this only covers the lighter,
read-side checks (claim/reject/callback/direct customer GET) that this
backend enforces itself before ever reaching catalog-service.
"""
import pytest
from fastapi import HTTPException

from app.services import authorization, catalog_client


def _detail(cust_nb="58466", salesman_id="sm1"):
    return catalog_client.CustomerDetail(
        cust_nb=cust_nb, customer_name="Test Co", email=None, telephone=None,
        city=None, address1=None, salesman_id=salesman_id)


class TestOwnsCustomer:
    def test_admin_owns_every_customer(self, admin, monkeypatch):
        monkeypatch.setattr(catalog_client, "get_customer_detail",
                            lambda cust_nb: (_ for _ in ()).throw(
                                AssertionError("admin path must not call catalog-service")))
        assert authorization.owns_customer("58466", admin) is True

    def test_unresolved_customer_is_always_ownable(self, salesman, monkeypatch):
        monkeypatch.setattr(catalog_client, "get_customer_detail",
                            lambda cust_nb: (_ for _ in ()).throw(
                                AssertionError("cust_nb=None must not call catalog-service")))
        assert authorization.owns_customer(None, salesman) is True

    def test_salesman_owns_their_assigned_customer(self, salesman, monkeypatch):
        monkeypatch.setattr(catalog_client, "get_customer_detail",
                            lambda cust_nb: _detail(cust_nb, salesman.login_id))
        assert authorization.owns_customer("58466", salesman) is True

    def test_salesman_does_not_own_another_salesmans_customer(self, salesman, monkeypatch):
        monkeypatch.setattr(catalog_client, "get_customer_detail",
                            lambda cust_nb: _detail(cust_nb, "someone_else"))
        assert authorization.owns_customer("58466", salesman) is False

    def test_salesman_does_not_own_unassigned_customer(self, salesman, monkeypatch):
        """salesman_id IS NULL (real ~40k un-migrated ERP customers, per
        the ownership feature's own notes) - admin-only, not fair game
        for any salesman just because nobody owns it yet."""
        monkeypatch.setattr(catalog_client, "get_customer_detail",
                            lambda cust_nb: _detail(cust_nb, None))
        assert authorization.owns_customer("58466", salesman) is False

    def test_nonexistent_customer_is_not_owned(self, salesman, monkeypatch):
        monkeypatch.setattr(catalog_client, "get_customer_detail",
                            lambda cust_nb: None)
        assert authorization.owns_customer("58466", salesman) is False


class TestRequireCustomerOwnership:
    def test_raises_403_with_default_detail_when_not_owned(self, salesman, monkeypatch):
        monkeypatch.setattr(catalog_client, "get_customer_detail",
                            lambda cust_nb: _detail(cust_nb, "someone_else"))
        with pytest.raises(HTTPException) as exc:
            authorization.require_customer_ownership("58466", salesman)
        assert exc.value.status_code == 403
        assert exc.value.detail == authorization.NOT_AUTHORIZED_DETAIL

    def test_raises_403_with_custom_detail(self, salesman, monkeypatch):
        monkeypatch.setattr(catalog_client, "get_customer_detail",
                            lambda cust_nb: _detail(cust_nb, "someone_else"))
        with pytest.raises(HTTPException) as exc:
            authorization.require_customer_ownership("58466", salesman,
                                                      detail="custom message")
        assert exc.value.detail == "custom message"

    def test_does_not_raise_when_owned(self, salesman, monkeypatch):
        monkeypatch.setattr(catalog_client, "get_customer_detail",
                            lambda cust_nb: _detail(cust_nb, salesman.login_id))
        authorization.require_customer_ownership("58466", salesman)  # no raise

    def test_does_not_raise_for_admin(self, admin, monkeypatch):
        monkeypatch.setattr(catalog_client, "get_customer_detail",
                            lambda cust_nb: (_ for _ in ()).throw(
                                AssertionError("admin path must not call catalog-service")))
        authorization.require_customer_ownership("58466", admin)  # no raise
