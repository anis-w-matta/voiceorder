"""voice buffer lead alias tables

Revision ID: 8c2319e65bc5
Revises: c26cc3476967
Create Date: 2026-08-10 09:12:10.189196

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '8c2319e65bc5'
down_revision: Union[str, None] = 'c26cc3476967'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "item_alias",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("item_number", sa.String(30),
                  sa.ForeignKey("item.item_number", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("alias", sa.String(300), nullable=False),
        sa.Column("lang", sa.String(10), server_default="ar"),
    )

    op.create_table(
        "voice_message",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("phone_raw", sa.String(50), nullable=False),
        sa.Column("phone_e164", sa.String(20), index=True),
        sa.Column("audio_path", sa.Text, nullable=False),
        sa.Column("duration_sec", sa.Numeric(8, 2)),
        sa.Column("transcript", sa.Text),
        sa.Column("transcript_conf", sa.Float),
        sa.Column("language", sa.String(10)),
        sa.Column("languages", postgresql.JSONB, server_default="[]"),
        sa.Column("segments", postgresql.JSONB, server_default="[]"),
        sa.Column("status", sa.String(20), server_default="received",
                  index=True),
        sa.Column("error", sa.Text),
        sa.Column("attempts", sa.Integer, server_default="0"),
        sa.Column("received_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "pending_request",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("voice_message_id", sa.BigInteger,
                  sa.ForeignKey("voice_message.id"), nullable=False),
        sa.Column("cust_nb", sa.String(20), index=True),
        sa.Column("intents", postgresql.JSONB, server_default="[]"),
        sa.Column("primary_intent", sa.String(40), nullable=False),
        sa.Column("target_order_nb", sa.String(30)),
        sa.Column("target_order_type", sa.String(10)),
        sa.Column("raw_model_output", postgresql.JSONB, server_default="{}"),
        sa.Column("flags", postgresql.JSONB, server_default="[]"),
        sa.Column("status", sa.String(20), server_default="new", index=True),
        sa.Column("assigned_to", sa.String(100)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.String(100)),
        sa.Column("decision_note", sa.Text),
        sa.Column("committed_order_nb", sa.String(30)),
    )

    op.create_table(
        "pending_request_line",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.BigInteger,
                  sa.ForeignKey("pending_request.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("line_nb", sa.Integer, nullable=False),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column("raw_lang", sa.String(10)),
        sa.Column("item_nb", sa.String(30)),
        sa.Column("item_desc", sa.String(300)),
        sa.Column("qty", sa.Numeric(12, 3)),
        sa.Column("uom", sa.String(20)),
        sa.Column("match_confidence", sa.Float),
        sa.Column("match_method", sa.String(20)),
        sa.Column("operator_edited", sa.Boolean, server_default="false"),
        sa.Column("candidates", postgresql.JSONB, server_default="[]"),
    )

    op.create_table(
        "lead",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("voice_message_id", sa.BigInteger,
                  sa.ForeignKey("voice_message.id"), nullable=False),
        sa.Column("phone_e164", sa.String(20), index=True),
        sa.Column("categories_sent", postgresql.JSONB, server_default="[]"),
        sa.Column("products_mentioned", postgresql.JSONB, server_default="[]"),
        sa.Column("catalogue_sent_at", sa.DateTime(timezone=True)),
        sa.Column("converted_cust_nb", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    for t in ("lead", "pending_request_line", "pending_request",
              "voice_message", "item_alias"):
        op.drop_table(t)
