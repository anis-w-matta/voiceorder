"""resolve_item() now delegates to catalog-service (item + the fuzzy
matching logic that depends on it both moved there - see
app/services/catalog_client.py) instead of running ItemResolver locally.
"""
from sqlalchemy.orm import Session

from app.services import catalog_client
from app.services.scripted.models import ItemMatchResult


def resolve_item(session: Session, item_span: str, **_ignored) -> ItemMatchResult:
    """`session`/any extra keyword args are accepted (and ignored) only to
    keep this call-site-compatible with its old local-DB signature -
    thresholds are now catalog-service's own settings."""
    return catalog_client.resolve_item(item_span)
