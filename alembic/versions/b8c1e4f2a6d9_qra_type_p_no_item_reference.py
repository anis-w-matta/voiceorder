"""qra_detail: type P has no item reference

Type P (price override) is synced from the source system with no buy/get
item pair at all - it applies qra_price to whatever item the salesman
ordered, gated only on qty_buy/uom_buy. Only types T/B actually need
item_nb_buy/item_nb_get/qty_get/uom_get, so those columns become nullable.

Revision ID: b8c1e4f2a6d9
Revises: 0447b46d90a9
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b8c1e4f2a6d9'
down_revision: Union[str, None] = '0447b46d90a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("qra_detail", "item_nb_buy", nullable=True)
    op.alter_column("qra_detail", "item_nb_get", nullable=True)
    op.alter_column("qra_detail", "qty_get", nullable=True)
    op.alter_column("qra_detail", "uom_get", nullable=True)


def downgrade() -> None:
    op.alter_column("qra_detail", "uom_get", nullable=False)
    op.alter_column("qra_detail", "qty_get", nullable=False)
    op.alter_column("qra_detail", "item_nb_get", nullable=False)
    op.alter_column("qra_detail", "item_nb_buy", nullable=False)
