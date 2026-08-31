"""qra_detail: drop id/uom_buy/uom_get, add item_nb_price, cust_nb becomes PK

A customer now has exactly one active QRA rule (T, B, or P) rather than
several simultaneous ones - cust_nb is unique per row going forward, same
shape qra_header already has. uom_buy/uom_get are dropped (unit matching
for qty_buy/qty_get now lives outside this app, per product direction);
item_nb_price is added so a type P price override scopes to one specific
item instead of "whatever item was ordered" (see app/models/qra.py).

Existing qra_detail rows are wiped rather than migrated: the dev DB
currently has 3 rows for one customer (58466 - one each of T/B/P, from
manual demo setup, see seed_qra_demo.py), which can't satisfy a
cust_nb-only primary key. This table has never shipped past local/demo
data - re-run whatever set that customer's QRA agreement up (a fresh type
P row now needs item_nb_price set) to restore the demo.

Revision ID: 1a8cec016ab9
Revises: c9a5797a0f22
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '1a8cec016ab9'
down_revision: Union[str, None] = 'c9a5797a0f22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM qra_detail")

    op.drop_constraint("qra_detail_pkey", "qra_detail", type_="primary")
    op.drop_column("qra_detail", "id")
    op.drop_column("qra_detail", "uom_buy")
    op.drop_column("qra_detail", "uom_get")
    op.add_column("qra_detail", sa.Column("item_nb_price", sa.String(30),
                                          nullable=True))
    op.create_primary_key("qra_detail_pkey", "qra_detail", ["cust_nb"])


def downgrade() -> None:
    op.execute("DELETE FROM qra_detail")

    op.drop_constraint("qra_detail_pkey", "qra_detail", type_="primary")
    op.drop_column("qra_detail", "item_nb_price")
    op.add_column("qra_detail", sa.Column(
        "id", sa.Integer, sa.Identity(), nullable=False))
    op.add_column("qra_detail", sa.Column(
        "uom_buy", sa.String(20), nullable=False, server_default="EACH"))
    op.add_column("qra_detail", sa.Column("uom_get", sa.String(20),
                                          nullable=True))
    op.alter_column("qra_detail", "uom_buy", server_default=None)
    op.create_primary_key("qra_detail_pkey", "qra_detail", ["id"])
