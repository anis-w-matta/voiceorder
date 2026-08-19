"""QA stress-test run/result tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qa_run",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("trigger", sa.String(20), server_default="manual"),
        sa.Column("tier", sa.String(20), server_default="local"),
        sa.Column("total", sa.Integer, server_default="0"),
        sa.Column("passed", sa.Integer, server_default="0"),
        sa.Column("failed", sa.Integer, server_default="0"),
        sa.Column("warnings", sa.Integer, server_default="0"),
        sa.Column("needs_review", sa.Integer, server_default="0"),
        sa.Column("not_run", sa.Integer, server_default="0"),
        sa.Column("critical_failures", sa.Integer, server_default="0"),
    )
    op.create_table(
        "qa_test_result",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.BigInteger,
                  sa.ForeignKey("qa_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_id", sa.String(150), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("subcategory", sa.String(50)),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("severity", sa.String(10), server_default="medium"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input", postgresql.JSONB, server_default="{}"),
        sa.Column("expected", postgresql.JSONB, server_default="{}"),
        sa.Column("actual", postgresql.JSONB, server_default="{}"),
        sa.Column("reason", sa.Text),
        sa.Column("debug", postgresql.JSONB, server_default="{}"),
        sa.Column("duration_ms", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
    op.create_index("idx_qa_test_result_run_id", "qa_test_result", ["run_id"])
    op.create_index("idx_qa_test_result_category", "qa_test_result", ["category"])
    op.create_index("idx_qa_test_result_status", "qa_test_result", ["status"])


def downgrade() -> None:
    op.drop_index("idx_qa_test_result_status", "qa_test_result")
    op.drop_index("idx_qa_test_result_category", "qa_test_result")
    op.drop_index("idx_qa_test_result_run_id", "qa_test_result")
    op.drop_table("qa_test_result")
    op.drop_table("qa_run")
