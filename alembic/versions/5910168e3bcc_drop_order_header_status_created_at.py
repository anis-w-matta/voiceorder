"""drop order_header.status and order_header.created_at

Both columns turned out to be dead weight: every OrderHeader row this app
has ever created sets status="open" and nothing ever sets it to anything
else (checked the live dev DB - all 16 rows are "open"), so the "open
orders" filtering in prior_order.py never actually filtered anything.
created_at existed only to answer two questions - "which order did this
customer place on date X" (get_order_nb_from_date) and "what's this
customer's most recent order" (PriorOrderService.last_order, reorder's
mode="last"/"date") - both of those features are removed in this change
along with the column, since there is no substitute timestamp to answer
them from. Reorder now always requires an explicit order number
(mode="order_nb").

Revision ID: 5910168e3bcc
Revises: 7877ff0e02e0
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '5910168e3bcc'
down_revision: Union[str, None] = '7877ff0e02e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("order_header", "status")
    op.drop_column("order_header", "created_at")


def downgrade() -> None:
    op.add_column("order_header", sa.Column(
        "created_at", sa.DateTime(timezone=True),
        server_default=sa.func.now(), nullable=False))
    op.add_column("order_header", sa.Column(
        "status", sa.String(20), nullable=False, server_default="open"))
