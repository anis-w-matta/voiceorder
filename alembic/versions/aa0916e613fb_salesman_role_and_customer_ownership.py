"""salesman.role - admin/salesman split for customer ownership

Backs the new Salesman<->Customer ownership feature: customer.salesman_id
itself lives in catalog-service's own schema (see that repo's
36869bd395d1 migration) since Customer lives there, not here. Every
existing account defaults to "salesman" so no currently-provisioned login
changes behavior; promoting one to "admin" is a manual follow-up (see
seed_admin.py), not part of this migration.

Revision ID: aa0916e613fb
Revises: a5b21f440dee
Create Date: 2026-08-31 06:42:55.903258

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aa0916e613fb'
down_revision: Union[str, None] = 'a5b21f440dee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("salesman", sa.Column(
        "role", sa.String(20), nullable=False,
        server_default="salesman"))


def downgrade() -> None:
    op.drop_column("salesman", "role")
