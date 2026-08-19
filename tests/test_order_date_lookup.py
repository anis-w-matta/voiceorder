from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.models import Customer, OrderDetail, OrderHeader
from app.services.prior_order import get_order_nb_from_date

# Fixed mid-day UTC instant rather than datetime.now(timezone.utc): the date
# lookup now resolves calendar days in the business's local timezone (see
# app.config.settings.business_timezone), not the raw UTC date, so a
# timestamp near the UTC day boundary would nondeterministically land on
# either side of the *local* boundary depending on when the suite happens to
# run. Noon UTC is safely inside a single calendar day for every real-world
# UTC offset.
TODAY = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)


def _mk_customer(session, nb):
    session.add(Customer(customer_number=nb, customer_name=f"Test {nb}"))
    session.flush()


def _mk_order(session, order_nb, cust_nb, when):
    session.add(OrderHeader(order_nb=order_nb, order_type="SO",
                            cust_nb=cust_nb, status="open",
                            source="test", created_at=when))
    session.flush()


# Use dedicated throwaway customer numbers (created and rolled back within
# each test's own transaction) rather than the shared seed_test.py
# customers, so the count of "orders today" for a customer is never
# affected by whatever real fixture/order data happens to already exist.

def test_existing_customer_and_date_returns_the_order_nb(db_session):
    _mk_customer(db_session, "ZDT001")
    _mk_order(db_session, "ZDT00001", "ZDT001", TODAY)
    assert get_order_nb_from_date(db_session, "ZDT001", TODAY.date()) == \
        "ZDT00001"


def test_existing_customer_no_order_on_that_date_returns_none(db_session):
    _mk_customer(db_session, "ZDT002")
    _mk_order(db_session, "ZDT00002", "ZDT002", TODAY)
    other_day = (TODAY - timedelta(days=30)).date()
    assert get_order_nb_from_date(db_session, "ZDT002", other_day) is None


def test_multiple_orders_same_date_returns_none_not_a_guess(db_session):
    _mk_customer(db_session, "ZDT003")
    _mk_order(db_session, "ZDT00003", "ZDT003", TODAY)
    _mk_order(db_session, "ZDT00004", "ZDT003", TODAY)
    assert get_order_nb_from_date(db_session, "ZDT003", TODAY.date()) is None


def test_order_for_customer_a_never_returned_for_customer_b(db_session):
    _mk_customer(db_session, "ZDT004")
    _mk_customer(db_session, "ZDT005")
    _mk_order(db_session, "ZDT00005", "ZDT004", TODAY)
    assert get_order_nb_from_date(db_session, "ZDT005", TODAY.date()) is None
    assert get_order_nb_from_date(db_session, "ZDT004", TODAY.date()) == \
        "ZDT00005"


def test_nonexistent_customer_returns_none(db_session):
    _mk_customer(db_session, "ZDT006")
    _mk_order(db_session, "ZDT00006", "ZDT006", TODAY)
    assert get_order_nb_from_date(db_session, "ZDT_NOPE", TODAY.date()) \
        is None


def test_nonexistent_date_returns_none(db_session):
    _mk_customer(db_session, "ZDT007")
    assert get_order_nb_from_date(db_session, "ZDT007", TODAY.date()) is None


def test_invalid_inputs_return_none_not_error(db_session):
    assert get_order_nb_from_date(db_session, "", TODAY.date()) is None
    assert get_order_nb_from_date(db_session, None, TODAY.date()) is None
    assert get_order_nb_from_date(db_session, "ZDT008", None) is None
    assert get_order_nb_from_date(db_session, None, None) is None


def test_empty_database_returns_none(db_session):
    # Wipe every order within this test's own (rolled-back-on-teardown)
    # transaction to exercise the true empty-database case.
    db_session.execute(delete(OrderDetail))
    db_session.execute(delete(OrderHeader))
    db_session.flush()
    assert get_order_nb_from_date(db_session, "C001", TODAY.date()) is None
