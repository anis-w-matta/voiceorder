"""resolver line flags and resolution meta

Revision ID: d1a2b3c4e5f6
Revises: a4f7e2c19b3d
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd1a2b3c4e5f6'
down_revision: Union[str, None] = 'a4f7e2c19b3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pending_request_line',
                  sa.Column('line_flags', postgresql.JSONB,
                            server_default='[]', nullable=False))
    op.add_column('pending_request_line',
                  sa.Column('resolution_meta', postgresql.JSONB,
                            server_default='{}', nullable=False))


def downgrade() -> None:
    op.drop_column('pending_request_line', 'resolution_meta')
    op.drop_column('pending_request_line', 'line_flags')
