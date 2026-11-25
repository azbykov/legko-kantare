"""campaign status (created | rollback_failed)

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('campaigns', sa.Column('status', sa.String(length=20), nullable=False, server_default='created'))


def downgrade() -> None:
    with op.batch_alter_table('campaigns') as batch:
        batch.drop_column('status')
