"""match_customer()/search_customers() now delegate to catalog-service
(customer + the fuzzy matching logic that depends on it both moved there
- see app/services/catalog_client.py) instead of scanning the customer
table locally.
"""
from sqlalchemy.orm import Session

from app.services import catalog_client
from app.services.scripted.models import CustomerMatch


def match_customer(session: Session, raw_text: str, **_ignored) -> CustomerMatch:
    """`session`/any extra keyword args are accepted (and ignored) only to
    keep this call-site-compatible with its old local-DB signature -
    thresholds are now catalog-service's own settings."""
    return catalog_client.match_customer(raw_text)


def search_customers(session: Session, q: str, limit: int = 5
                     ) -> list[tuple[str, str, float]]:
    return catalog_client.search_customers(q)[:limit]
