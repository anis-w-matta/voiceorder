"""existing table additions

Revision ID: c26cc3476967
Revises:
Create Date: 2026-08-10 09:11:08.601635

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c26cc3476967'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # customer, item, order_header, order_details do not exist yet in this
    # fresh database -- create them here matching Steps 21-23, then add the
    # voice-intake columns on top.
    op.create_table(
        "customer",
        sa.Column("CustomerNumber", sa.String(20), primary_key=True),
        sa.Column("CustomerName", sa.String(200), nullable=False),
        sa.Column("email", sa.String(200)),
        sa.Column("telephone", sa.String(50)),
        sa.Column("City", sa.String(100)),
        sa.Column("Address1", sa.String(200)),
        sa.Column("Address2", sa.String(200)),
    )

    op.create_table(
        "item",
        sa.Column("item_number", sa.String(30), primary_key=True),
        sa.Column("item_desc", sa.String(300), nullable=False),
        sa.Column("category", sa.String(100), nullable=False, index=True),
    )

    op.create_table(
        "order_header",
        sa.Column("order_nb", sa.String(30), primary_key=True),
        sa.Column("order_type", sa.String(10), primary_key=True),
        sa.Column("cust_nb", sa.String(20), nullable=False, index=True),
    )

    op.create_table(
        "order_details",
        sa.Column("order_nb", sa.String(30), primary_key=True),
        sa.Column("order_type", sa.String(10), primary_key=True),
        sa.Column("line_nb", sa.Integer, primary_key=True),
        sa.Column("item_nb", sa.String(30), nullable=False),
        sa.Column("item_desc", sa.String(300), nullable=False),
        sa.Column("qty", sa.Numeric(12, 3), nullable=False),
        sa.Column("uom", sa.String(20), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_nb", "order_type"],
            ["order_header.order_nb", "order_header.order_type"]),
    )

    op.add_column("customer", sa.Column("phone_e164", sa.String(20)))
    op.create_index("idx_customer_phone", "customer", ["phone_e164"])
    op.add_column("order_header",
                  sa.Column("status", sa.String(20), server_default="open"))
    op.add_column("order_header",
                  sa.Column("created_at", sa.DateTime(timezone=True),
                            server_default=sa.func.now()))
    op.add_column("order_header",
                  sa.Column("source", sa.String(20), server_default="manual"))
    op.execute("CREATE SEQUENCE IF NOT EXISTS order_nb_seq START 1")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS order_nb_seq")
    op.drop_column("order_header", "source")
    op.drop_column("order_header", "created_at")
    op.drop_column("order_header", "status")
    op.drop_index("idx_customer_phone", "customer")
    op.drop_column("customer", "phone_e164")
    op.drop_table("order_details")
    op.drop_table("order_header")
    op.drop_table("item")
    op.drop_table("customer")
