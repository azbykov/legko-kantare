"""campaigns.country (одна страна) → campaigns.countries (список ISO-кодов, JSON)

Revision ID: 0004
Revises: 0003
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("campaigns") as batch:
        batch.add_column(sa.Column("countries", sa.JSON(), nullable=True))
    conn = op.get_bind()
    for row_id, country in conn.execute(sa.text("SELECT id, country FROM campaigns")).all():
        conn.execute(
            sa.text("UPDATE campaigns SET countries = :c WHERE id = :id"),
            {"c": json.dumps([country]), "id": row_id},
        )
    with op.batch_alter_table("campaigns") as batch:
        batch.alter_column("countries", existing_type=sa.JSON(), nullable=False)
        batch.drop_column("country")


def downgrade() -> None:
    with op.batch_alter_table("campaigns") as batch:
        batch.add_column(sa.Column("country", sa.String(length=2), nullable=True))
    conn = op.get_bind()
    for row_id, countries in conn.execute(sa.text("SELECT id, countries FROM campaigns")).all():
        codes = json.loads(countries) if isinstance(countries, str) else countries
        conn.execute(
            sa.text("UPDATE campaigns SET country = :c WHERE id = :id"),
            {"c": (codes or ["??"])[0], "id": row_id},  # в старую схему влезает только первая страна
        )
    with op.batch_alter_table("campaigns") as batch:
        batch.alter_column("country", existing_type=sa.String(length=2), nullable=False)
        batch.drop_column("countries")
