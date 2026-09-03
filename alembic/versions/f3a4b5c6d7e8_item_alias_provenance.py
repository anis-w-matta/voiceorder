"""item_alias provenance and normalized_alias

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-08-13 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.services.normalization import normalize_text

# revision identifiers, used by Alembic.
revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('item_alias',
                  sa.Column('source', sa.String(20), server_default='seed',
                            nullable=False))
    op.add_column('item_alias', sa.Column('confidence', sa.Float))
    op.add_column('item_alias',
                  sa.Column('created_at', sa.DateTime(timezone=True),
                            server_default=sa.func.now()))
    op.add_column('item_alias',
                  sa.Column('updated_at', sa.DateTime(timezone=True),
                            server_default=sa.func.now()))
    op.add_column('item_alias', sa.Column('normalized_alias', sa.String(300)))

    # Backfill using the exact same normalize_text() the app uses at query
    # time (lowercase + strip punctuation), not a SQL approximation - a
    # divergence here means exact-match alias lookups silently miss for any
    # alias containing punctuation.
    item_alias = sa.table(
        'item_alias', sa.column('id', sa.Integer),
        sa.column('alias', sa.String), sa.column('normalized_alias', sa.String))
    bind = op.get_bind()
    for row in bind.execute(sa.select(item_alias.c.id, item_alias.c.alias)):
        bind.execute(
            item_alias.update().where(item_alias.c.id == row.id)
            .values(normalized_alias=normalize_text(row.alias)))

    op.alter_column('item_alias', 'normalized_alias', nullable=False)
    op.create_index('idx_item_alias_normalized', 'item_alias',
                    ['normalized_alias'])


def downgrade() -> None:
    op.drop_index('idx_item_alias_normalized', 'item_alias')
    for col in ('normalized_alias', 'updated_at', 'created_at', 'confidence',
               'source'):
        op.drop_column('item_alias', col)
