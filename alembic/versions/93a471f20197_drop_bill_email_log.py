"""drop bill_email_log (billing subsystem removed)

Revision ID: 93a471f20197
Revises: d4e5f6a7b8c9
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '93a471f20197'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("bill_email_log")


def downgrade() -> None:
    op.create_table(
        "bill_email_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("cust_nb", sa.String(20), nullable=False),
        sa.Column("order_nb", sa.String(30), nullable=False),
        sa.Column("order_type", sa.String(10), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("cust_nb", "order_nb", "order_type",
                            name="uq_bill_email_log_pair"),
    )
