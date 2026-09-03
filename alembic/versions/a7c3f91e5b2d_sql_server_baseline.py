"""SQL Server baseline - fresh schema for the Postgres -> SQL Server 2025
migration, replacing the 25 Postgres-targeted migrations previously in this
directory (moved to alembic/versions_postgres_legacy/ for reference, not
deleted - they no longer apply since their DDL is Postgres-specific and
this service no longer runs on Postgres).

Reflects the CURRENT final shape of app/models/ (activity_log, salesman,
voice_message, pending_request, pending_request_line) directly, the same
"start from the final shape, not a replayed history" approach
catalog-service's own initial_schema migration already used for the same
reason (see that repo's 3aa07b9dbc40).

Not carried forward: the Postgres-only `order_nb_seq` sequence catalog
history created here. Confirmed dead code on the backend side (nothing
calls OrderNumberService.next() in the live commit path - see
app/services/numbering.py and app/services/commit.py) - catalog-service
owns the one sequence that's actually used, ported separately in that
service's own migration.

This is a genuinely new baseline: run against an empty SQL Server database
only. It has not been executed against a live SQL Server instance (none
was reachable in the environment this was written in) - review the DDL
and run `alembic upgrade head` against a real target before trusting it,
the same way any hand-written migration should be verified.

Revision ID: a7c3f91e5b2d
Revises:
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3f91e5b2d'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "salesman",
        sa.Column("login_id", sa.String(50), primary_key=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False,
                  server_default=sa.true()),
        # "salesman" (restricted to own assigned customers) or "admin"
        # (bypasses ownership - see app/services/authorization.py).
        sa.Column("role", sa.String(20), nullable=False,
                  server_default="salesman"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("SYSUTCDATETIME()")),
    )

    op.create_table(
        "voice_message",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("phone_raw", sa.String(50), nullable=False),
        sa.Column("audio_path", sa.Text, nullable=False),
        sa.Column("duration_sec", sa.Numeric(8, 2)),
        sa.Column("transcript", sa.Text),
        sa.Column("normalized_transcript", sa.Text),
        sa.Column("transcript_quality", sa.String(20),
                  server_default="good"),
        sa.Column("transcription_disagreement", sa.Boolean,
                  server_default=sa.false()),
        sa.Column("transcript_attempts", sa.JSON, server_default="[]"),
        sa.Column("transcript_conf", sa.Float),
        sa.Column("language", sa.String(10)),
        sa.Column("languages", sa.JSON, server_default="[]"),
        sa.Column("segments", sa.JSON, server_default="[]"),
        sa.Column("status", sa.String(20), server_default="received",
                  index=True),
        # "server" (default, Gemini transcribes) or "client_whisper" (the
        # Android app already transcribed on-device) - see app/pipeline.py.
        sa.Column("transcript_source", sa.String(20),
                  server_default="server"),
        sa.Column("error", sa.Text),
        sa.Column("attempts", sa.Integer, server_default="0"),
        sa.Column("received_at", sa.DateTime(timezone=True),
                  server_default=sa.text("SYSUTCDATETIME()")),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "pending_request",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("voice_message_id", sa.BigInteger,
                  sa.ForeignKey("voice_message.id"), nullable=False),
        sa.Column("cust_nb", sa.String(20), index=True),
        sa.Column("intents", sa.JSON, server_default="[]"),
        sa.Column("primary_intent", sa.String(40), nullable=False),
        sa.Column("target_order_nb", sa.String(30)),
        sa.Column("target_order_type", sa.String(10)),
        sa.Column("raw_model_output", sa.JSON, server_default="{}"),
        sa.Column("flags", sa.JSON, server_default="[]"),
        sa.Column("classification_quality", sa.String(20),
                  server_default="good"),
        sa.Column("status", sa.String(20), server_default="new", index=True),
        sa.Column("assigned_to", sa.String(100)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("SYSUTCDATETIME()")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.String(100)),
        sa.Column("decision_note", sa.Text),
        sa.Column("committed_order_nb", sa.String(30)),
        # The commit saga's idempotency key - see
        # app/services/commit.py's OrderCommitService.commit() and
        # app/worker.py's reconcile_stuck_commits(). Uniqueness is enforced
        # by a filtered index below, not unique=True here: a plain unique
        # constraint on this nullable column would let SQL Server accept
        # only one NULL row total (unlike Postgres), and almost every
        # pending request has commit_intent_id=None until it starts
        # committing.
        sa.Column("commit_intent_id", sa.String(36)),
    )
    op.create_index(
        "ix_pending_request_commit_intent_id", "pending_request",
        ["commit_intent_id"], unique=True,
        mssql_where=sa.text("commit_intent_id IS NOT NULL"))

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
        # add/remove/increase/decrease - only meaningful for update_order/
        # repeat_order_adjusted lines.
        sa.Column("change", sa.String(10)),
        sa.Column("operator_edited", sa.Boolean, server_default=sa.false()),
        sa.Column("candidates", sa.JSON, server_default="[]"),
        sa.Column("category", sa.String(100)),
        sa.Column("line_flags", sa.JSON, server_default="[]"),
        sa.Column("resolution_meta", sa.JSON, server_default="{}"),
        sa.Column("attributes", sa.JSON, server_default="{}"),
        sa.Column("qualifiers", sa.JSON, server_default="{}"),
    )

    op.create_table(
        "activity_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True),
                  server_default=sa.text("SYSUTCDATETIME()"), index=True),
        sa.Column("event_type", sa.String(40), nullable=False, index=True),
        sa.Column("level", sa.String(10), server_default="info"),
        sa.Column("voice_message_id", sa.BigInteger, index=True),
        sa.Column("request_id", sa.BigInteger, index=True),
        sa.Column("cust_nb", sa.String(20), index=True),
        sa.Column("order_nb", sa.String(30)),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("details", sa.JSON, server_default="{}"),
    )


def downgrade() -> None:
    for t in ("activity_log", "pending_request_line", "pending_request",
             "voice_message", "salesman"):
        op.drop_table(t)
