"""drop phone_e164, order_header.source, and key qra_header by cust_nb

phone_e164 (customer, voice_message) was stored/displayed but never used
to auto-match anything - customer resolution (match_customer.py) matches
on name/customer_number only.

order_header.source was set once at commit time ("voice") and never read
anywhere. order_header.status stays - prior_order.py's
open_orders()/resolve_target() (reorder disambiguation) and the SQL
injection safety tests in test_reorder_injection.py depend on it.

qra_header.id becomes cust_nb: each customer has at most one QRA
agreement, so cust_nb is now the primary key and a real FK to customer,
and qra_detail drops its qra_header_id column in favor of joining on the
cust_nb it already carried (previously just a denormalized copy).

Revision ID: 0447b46d90a9
Revises: d4e8f1a2b3c5
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0447b46d90a9'
down_revision: Union[str, None] = 'd4e8f1a2b3c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("order_header", "source")

    op.drop_index("idx_customer_phone", "customer")
    op.drop_column("customer", "phone_e164")

    op.drop_index("ix_voice_message_phone_e164", "voice_message")
    op.drop_column("voice_message", "phone_e164")

    op.drop_constraint("qra_detail_qra_header_id_fkey", "qra_detail",
                       type_="foreignkey")
    op.drop_index("ix_qra_detail_qra_header_id", "qra_detail")
    op.drop_column("qra_detail", "qra_header_id")

    op.drop_index("ix_qra_header_cust_nb", "qra_header")
    op.drop_constraint("qra_header_pkey", "qra_header", type_="primary")
    op.drop_column("qra_header", "id")
    op.create_primary_key("qra_header_pkey", "qra_header", ["cust_nb"])
    op.create_foreign_key(
        "qra_header_cust_nb_fkey", "qra_header", "customer",
        ["cust_nb"], ["CustomerNumber"])
    op.create_foreign_key(
        "qra_detail_cust_nb_fkey", "qra_detail", "qra_header",
        ["cust_nb"], ["cust_nb"], ondelete="CASCADE")


def downgrade() -> None:
    op.drop_constraint("qra_detail_cust_nb_fkey", "qra_detail",
                       type_="foreignkey")
    op.drop_constraint("qra_header_cust_nb_fkey", "qra_header",
                       type_="foreignkey")
    op.drop_constraint("qra_header_pkey", "qra_header", type_="primary")

    op.add_column("qra_header", sa.Column(
        "id", sa.Integer, sa.Identity(), nullable=False))
    op.create_primary_key("qra_header_pkey", "qra_header", ["id"])
    op.create_index("ix_qra_header_cust_nb", "qra_header", ["cust_nb"])

    op.add_column("qra_detail", sa.Column("qra_header_id", sa.Integer))
    op.execute("""
        UPDATE qra_detail d SET qra_header_id = h.id
        FROM qra_header h WHERE h.cust_nb = d.cust_nb
    """)
    op.alter_column("qra_detail", "qra_header_id", nullable=False)
    op.create_foreign_key(
        "qra_detail_qra_header_id_fkey", "qra_detail", "qra_header",
        ["qra_header_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_qra_detail_qra_header_id", "qra_detail",
                    ["qra_header_id"])

    op.add_column("order_header", sa.Column(
        "source", sa.String(20), nullable=False, server_default="manual"))

    op.add_column("customer", sa.Column("phone_e164", sa.String(20)))
    op.create_index("idx_customer_phone", "customer", ["phone_e164"])

    op.add_column("voice_message", sa.Column("phone_e164", sa.String(20)))
    op.create_index("ix_voice_message_phone_e164", "voice_message",
                    ["phone_e164"])
