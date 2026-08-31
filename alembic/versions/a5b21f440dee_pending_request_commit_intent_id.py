"""pending_request.commit_intent_id - the commit saga's idempotency key

Also drops item/customer/order_header/order_details/qra_header/qra_detail
from this backend's model registry (see app/models/__init__.py) now that
they're owned by catalog-service - but NOT dropped from the database
here. Leaving the tables in place for now is a deliberate safety window
(same pattern used for every prior column-drop this session): only after
the catalog-service split is confirmed working end-to-end should a
follow-up migration actually DROP these tables from this database.

Revision ID: a5b21f440dee
Revises: 1a8cec016ab9
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a5b21f440dee'
down_revision: Union[str, None] = '1a8cec016ab9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pending_request", sa.Column(
        "commit_intent_id", sa.String(36), nullable=True))
    op.create_index("ix_pending_request_commit_intent_id", "pending_request",
                    ["commit_intent_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_pending_request_commit_intent_id",
                  table_name="pending_request")
    op.drop_column("pending_request", "commit_intent_id")
