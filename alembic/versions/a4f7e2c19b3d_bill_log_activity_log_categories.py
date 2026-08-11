"""bill email log, activity log, item classification columns

Revision ID: a4f7e2c19b3d
Revises: 071679317c1a
Create Date: 2026-08-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a4f7e2c19b3d'
down_revision: Union[str, None] = '071679317c1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pending_request_line',
                  sa.Column('category', sa.String(100), nullable=True))
    op.add_column('order_details',
                  sa.Column('category', sa.String(100), nullable=True))

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

    op.create_table(
        "activity_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("level", sa.String(10), server_default="info"),
        sa.Column("voice_message_id", sa.BigInteger),
        sa.Column("request_id", sa.BigInteger),
        sa.Column("cust_nb", sa.String(20)),
        sa.Column("order_nb", sa.String(30)),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("details", postgresql.JSONB, server_default="{}"),
    )
    op.create_index("idx_activity_log_ts", "activity_log", ["ts"])
    op.create_index("idx_activity_log_event_type", "activity_log",
                    ["event_type"])
    op.create_index("idx_activity_log_voice_message_id", "activity_log",
                    ["voice_message_id"])
    op.create_index("idx_activity_log_request_id", "activity_log",
                    ["request_id"])
    op.create_index("idx_activity_log_cust_nb", "activity_log", ["cust_nb"])


def downgrade() -> None:
    op.drop_index("idx_activity_log_cust_nb", "activity_log")
    op.drop_index("idx_activity_log_request_id", "activity_log")
    op.drop_index("idx_activity_log_voice_message_id", "activity_log")
    op.drop_index("idx_activity_log_event_type", "activity_log")
    op.drop_index("idx_activity_log_ts", "activity_log")
    op.drop_table("activity_log")
    op.drop_table("bill_email_log")
    op.drop_column("order_details", "category")
    op.drop_column("pending_request_line", "category")
