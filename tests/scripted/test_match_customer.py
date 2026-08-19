import pytest

from app.models import Customer
from app.services.scripted.match_customer import match_customer
from app.services.scripted.models import MatchStatus


@pytest.fixture
def customers(db_session):
    # db_session rolls back at test end (see conftest.py) - no explicit
    # cleanup needed.
    db_session.add(Customer(customer_number="ZZC1", customer_name="Zzcorp Trading"))
    # Same name under two different customer numbers - a genuine tie
    # regardless of the tie-margin threshold, exercising the "never
    # silently pick one" rule (spec section 16).
    db_session.add(Customer(customer_number="ZZC2", customer_name="Zzcorp Trading"))
    db_session.add(Customer(customer_number="ZZC3", customer_name="Different Distributors"))
    db_session.flush()


def test_exact_match_by_number(db_session, customers):
    r = match_customer(db_session, "ZZC3")
    assert r.status == MatchStatus.MATCHED
    assert r.customer_number == "ZZC3"


def test_minor_typo_still_matches(db_session, customers):
    r = match_customer(db_session, "Different Distributor")
    assert r.status == MatchStatus.MATCHED
    assert r.customer_number == "ZZC3"


def test_case_difference_matches(db_session, customers):
    r = match_customer(db_session, "different distributors")
    assert r.status == MatchStatus.MATCHED
    assert r.customer_number == "ZZC3"


def test_ambiguous_when_two_names_are_near_tied(db_session, customers):
    r = match_customer(db_session, "Zzcorp Trading")
    assert r.status == MatchStatus.AMBIGUOUS
    assert r.customer_number is None


def test_unknown_customer_not_found(db_session, customers):
    r = match_customer(db_session, "Totally Unrelated Nonexistent Company Name")
    assert r.status == MatchStatus.NOT_FOUND
    assert r.customer_number is None


def test_empty_text_not_found(db_session, customers):
    r = match_customer(db_session, "")
    assert r.status == MatchStatus.NOT_FOUND
