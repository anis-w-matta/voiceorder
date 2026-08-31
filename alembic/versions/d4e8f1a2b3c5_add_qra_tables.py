"""add QRA (quantity rebate agreement) tables and order_details columns

Adds qra_header/qra_detail (synced in from an external system, never
written by this app - see app/services/qra_engine.py) and 3 columns on
order_details that record whether/how a line was affected by an
agreement at commit time: line_type (always "S" today), is_free, and
qra_detail_id.

Revision ID: d4e8f1a2b3c5
Revises: 7ca52bcb8221
Create Date: 2026-08-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4e8f1a2b3c5'
down_revision: Union[str, None] = '7ca52bcb8221'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qra_header",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("cust_nb", sa.String(20), nullable=False),
        sa.Column("from_date", sa.Date, nullable=False),
        sa.Column("to_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="active"),
    )
    op.create_index("ix_qra_header_cust_nb", "qra_header", ["cust_nb"])

    op.create_table(
        "qra_detail",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("qra_header_id", sa.Integer,
                  sa.ForeignKey("qra_header.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("cust_nb", sa.String(20), nullable=False),
        sa.Column("qra_type", sa.String(1), nullable=False),
        sa.Column("item_nb_buy", sa.String(30), nullable=False),
        sa.Column("item_nb_get", sa.String(30), nullable=False),
        sa.Column("qty_buy", sa.Numeric(12, 3), nullable=False),
        sa.Column("qty_get", sa.Numeric(12, 3), nullable=False),
        sa.Column("uom_buy", sa.String(20), nullable=False),
        sa.Column("uom_get", sa.String(20), nullable=False),
        sa.Column("qra_price", sa.Numeric(12, 2)),
    )
    op.create_index("ix_qra_detail_qra_header_id", "qra_detail",
                    ["qra_header_id"])
    op.create_index("ix_qra_detail_cust_nb", "qra_detail", ["cust_nb"])
    op.create_index("ix_qra_detail_item_nb_buy", "qra_detail", ["item_nb_buy"])

    op.add_column("order_details", sa.Column(
        "line_type", sa.String(1), nullable=False, server_default="S"))
    op.add_column("order_details", sa.Column(
        "is_free", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("order_details", sa.Column(
        "qra_detail_id", sa.Integer,
        sa.ForeignKey("qra_detail.id"), nullable=True))


def downgrade() -> None:
    op.drop_column("order_details", "qra_detail_id")
    op.drop_column("order_details", "is_free")
    op.drop_column("order_details", "line_type")

    op.drop_index("ix_qra_detail_item_nb_buy", "qra_detail")
    op.drop_index("ix_qra_detail_cust_nb", "qra_detail")
    op.drop_index("ix_qra_detail_qra_header_id", "qra_detail")
    op.drop_table("qra_detail")

    op.drop_index("ix_qra_header_cust_nb", "qra_header")
    op.drop_table("qra_header")
