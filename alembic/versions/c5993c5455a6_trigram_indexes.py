"""trigram indexes

Revision ID: c5993c5455a6
Revises: 8c2319e65bc5
Create Date: 2026-08-10 09:12:43.821395

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5993c5455a6'
down_revision: Union[str, None] = '8c2319e65bc5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX idx_item_desc_trgm ON item "
               "USING gin (item_desc gin_trgm_ops)")
    op.execute("CREATE INDEX idx_item_alias_trgm ON item_alias "
               "USING gin (alias gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_item_alias_trgm")
    op.execute("DROP INDEX IF EXISTS idx_item_desc_trgm")
