"""transcript quality tracking on voice_message

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-13 00:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('voice_message', sa.Column('normalized_transcript', sa.Text))
    op.add_column('voice_message',
                  sa.Column('transcript_quality', sa.String(20),
                            server_default='good', nullable=False))
    op.add_column('voice_message',
                  sa.Column('transcription_disagreement', sa.Boolean,
                            server_default='false', nullable=False))
    op.add_column('voice_message',
                  sa.Column('transcript_attempts', postgresql.JSONB,
                            server_default='[]', nullable=False))


def downgrade() -> None:
    op.drop_column('voice_message', 'transcript_attempts')
    op.drop_column('voice_message', 'transcription_disagreement')
    op.drop_column('voice_message', 'transcript_quality')
    op.drop_column('voice_message', 'normalized_transcript')
