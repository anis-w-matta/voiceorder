"""drop order_details.unit_price, .category, .qra_detail_id

None of these three are mapped on the OrderDetail model any more, and
haven't been for a while - OrderCommitService.commit() (app/services/
commit.py) already builds every OrderDetail row without them, using only
QraLineEffect.is_free from qra_engine.apply_qra(). unit_price/category
were never populated at commit time to begin with (pricing/categorisation
live on Item, not the order line), and qra_detail_id tracked which QRA
detail produced a line's effect - useful for the pre-commit *preview*
(QraLinePreview, still fully in place, unchanged), but nothing ever read
it back off a committed OrderDetail row. tests/test_qra.py already
documents this: "OrderDetail carries no price/QRA trace at all once
committed".

Revision ID: c9a5797a0f22
Revises: 5910168e3bcc
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c9a5797a0f22'
down_revision: Union[str, None] = '5910168e3bcc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("order_details_qra_detail_id_fkey", "order_details",
                       type_="foreignkey")
    op.drop_column("order_details", "qra_detail_id")
    op.drop_column("order_details", "unit_price")
    op.drop_column("order_details", "category")


def downgrade() -> None:
    op.add_column("order_details", sa.Column(
        "category", sa.String(100), nullable=True))
    op.add_column("order_details", sa.Column(
        "unit_price", sa.Numeric(12, 2), nullable=True))
    op.add_column("order_details", sa.Column(
        "qra_detail_id", sa.Integer, nullable=True))
    op.create_foreign_key(
        "order_details_qra_detail_id_fkey", "order_details", "qra_detail",
        ["qra_detail_id"], ["id"])
