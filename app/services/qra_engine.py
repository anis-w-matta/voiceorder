"""QRA preview now delegates to catalog-service (qra_header/qra_detail
moved there - see app/services/catalog_client.py). Real QRA application
at commit time also now happens inside catalog-service's POST /orders
(app/services/commit.py's saga just sends the raw resolved lines and
gets back the final ones, QRA-applied) - there is no local apply_qra any
more.
"""
from dataclasses import dataclass
from decimal import Decimal

from app.services import catalog_client


@dataclass
class QraLinePreview:
    """One line's QRA preview, for RequestDetail (see api/queue.py) - shown
    on the pending-request review screen before Accept, since QRA only
    actually applies at commit time and there's no committed-order screen
    yet for a reviewer to see the effect otherwise."""
    line_nb: int
    unit_price: Decimal | None
    is_free: bool
    substituted_item_nb: str | None
    substituted_item_desc: str | None


@dataclass
class QraBonusLinePreview:
    item_nb: str
    item_desc: str
    qty: Decimal
    uom: str | None


def preview_qra(session, cust_nb: str | None, lines,
                is_return: bool = False
                ) -> tuple[list[QraLinePreview], list[QraBonusLinePreview]]:
    """Read-only preview of what committing `lines` would do under the
    customer's active QRA rule - never mutates `lines`, never writes
    anything. `session` is accepted (and ignored) only to keep this call
    site-compatible with its old local-DB signature.
    """
    previews, bonuses = catalog_client.preview_qra(cust_nb, lines,
                                                    is_return=is_return)
    return (
        [QraLinePreview(line_nb=p.line_nb, unit_price=p.unit_price,
                        is_free=p.is_free,
                        substituted_item_nb=p.substituted_item_nb,
                        substituted_item_desc=p.substituted_item_desc)
        for p in previews],
        [QraBonusLinePreview(item_nb=b.item_nb, item_desc=b.item_desc,
                             qty=b.qty, uom=b.uom)
        for b in bonuses],
    )
