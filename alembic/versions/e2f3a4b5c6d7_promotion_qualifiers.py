"""promotion codes and structured qualifiers/attributes

Revision ID: e2f3a4b5c6d7
Revises: d1a2b3c4e5f6
Create Date: 2026-08-13 00:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, None] = 'd1a2b3c4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "promotion_code",
        sa.Column("code", sa.String(30), primary_key=True),
        sa.Column("discount_percent", sa.Numeric(5, 2)),
        sa.Column("discount_amount", sa.Numeric(12, 2)),
        sa.Column("discount_type", sa.String(10), nullable=False),
        sa.Column("description", sa.String(200)),
        sa.Column("active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
    op.execute("""
        INSERT INTO promotion_code (code, discount_percent, discount_type, description)
        VALUES ('SV20', 20, 'percent', 'seed promotion'),
               ('DIS40', 40, 'percent', 'seed promotion')
    """)
    op.add_column('pending_request_line',
                  sa.Column('attributes', postgresql.JSONB,
                            server_default='{}', nullable=False))
    op.add_column('pending_request_line',
                  sa.Column('qualifiers', postgresql.JSONB,
                            server_default='{}', nullable=False))


def downgrade() -> None:
    op.drop_column('pending_request_line', 'qualifiers')
    op.drop_column('pending_request_line', 'attributes')
    op.drop_table("promotion_code")
