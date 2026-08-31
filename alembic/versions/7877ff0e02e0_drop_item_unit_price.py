"""drop item.unit_price

Item no longer carries a price of its own - the only source of price
anywhere in the app is a QRA agreement's qra_price (see
app/services/qra_engine.py). OrderDetail.unit_price is unaffected: it
still snapshots whatever price applied at commit time, now sourced solely
from the QRA effect rather than falling back to Item.unit_price.

Revision ID: 7877ff0e02e0
Revises: 449c1c906190
Create Date: 2026-08-26 13:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7877ff0e02e0'
down_revision: Union[str, None] = '449c1c906190'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('item', 'unit_price')


def downgrade() -> None:
    op.add_column('item', sa.Column('unit_price', sa.Numeric(12, 2)))
