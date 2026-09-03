"""add voice_message.transcript_source (client-side whisper.cpp support)

Revision ID: a3b7d1c9f204
Revises: f86ca96e8728
Create Date: 2026-08-18 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a3b7d1c9f204'
down_revision: Union[str, None] = 'f86ca96e8728'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "voice_message",
        sa.Column("transcript_source", sa.String(20), nullable=False,
                  server_default="server"))


def downgrade() -> None:
    op.drop_column("voice_message", "transcript_source")
