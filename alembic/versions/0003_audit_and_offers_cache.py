"""audit_log (actor, before/after, error) вместо offer_changes; offers_cache

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('actor', sa.String(length=255), nullable=False),
        sa.Column('action', sa.String(length=32), nullable=False),
        sa.Column('campaign_id', sa.Integer(), nullable=True),
        sa.Column('stream_id', sa.Integer(), nullable=True),
        sa.Column('offer_id', sa.Integer(), nullable=True),
        sa.Column('before', sa.JSON(), nullable=True),
        sa.Column('after', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_audit_log_action', 'audit_log', ['action'])
    op.create_index('ix_audit_log_campaign_id', 'audit_log', ['campaign_id'])
    # перенос старого аудита: actor неизвестен, снимков не было
    op.execute(
        "INSERT INTO audit_log (actor, action, campaign_id, stream_id, offer_id, created_at) "
        "SELECT 'unknown', 'offer.' || action, campaign_id, stream_id, offer_id, created_at FROM offer_changes"
    )
    op.drop_index('ix_offer_changes_campaign_id', table_name='offer_changes')
    op.drop_table('offer_changes')

    op.create_table(
        'offers_cache',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('country', sa.JSON(), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('offers_cache')
    op.create_table(
        'offer_changes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('stream_id', sa.Integer(), nullable=False),
        sa.Column('offer_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_offer_changes_campaign_id', 'offer_changes', ['campaign_id'])
    op.execute(
        "INSERT INTO offer_changes (campaign_id, stream_id, offer_id, action, created_at) "
        "SELECT campaign_id, stream_id, offer_id, substr(action, 7), created_at FROM audit_log "
        "WHERE action IN ('offer.add', 'offer.remove') AND stream_id IS NOT NULL AND offer_id IS NOT NULL"
    )
    op.drop_index('ix_audit_log_campaign_id', table_name='audit_log')
    op.drop_index('ix_audit_log_action', table_name='audit_log')
    op.drop_table('audit_log')
