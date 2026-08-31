"""drop item_alias

Item resolution no longer uses a learned/seeded alias table - matching is
exact item_number/item_desc plus pg_trgm+rapidfuzz fuzzy scoring against
item.item_desc only (app/services/item_resolver.py). The alias-learning
feedback loop (app/services/alias_learning.py) is removed along with it.

Revision ID: 449c1c906190
Revises: b8c1e4f2a6d9
Create Date: 2026-08-26 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '449c1c906190'
down_revision: Union[str, None] = 'b8c1e4f2a6d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("item_alias")


def downgrade() -> None:
    op.create_table(
        "item_alias",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("item_number", sa.String(30),
                  sa.ForeignKey("item.item_number", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("alias", sa.String(300), nullable=False),
        sa.Column("lang", sa.String(10), server_default="ar"),
        sa.Column("source", sa.String(20), server_default="seed",
                  nullable=False),
        sa.Column("confidence", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("normalized_alias", sa.String(300), nullable=False),
    )
    op.create_index("idx_item_alias_normalized", "item_alias",
                    ["normalized_alias"])
    op.execute("CREATE INDEX idx_item_alias_trgm ON item_alias "
               "USING gin (alias gin_trgm_ops)")
