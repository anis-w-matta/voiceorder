"""add item and order_detail pricing

Revision ID: 071679317c1a
Revises: c5993c5455a6
Create Date: 2026-08-10 16:10:20.766882

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '071679317c1a'
down_revision: Union[str, None] = 'c5993c5455a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('item', sa.Column('unit_price', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column('order_details', sa.Column('unit_price', sa.Numeric(precision=12, scale=2), nullable=True))


def downgrade() -> None:
    op.drop_column('order_details', 'unit_price')
    op.drop_column('item', 'unit_price')
