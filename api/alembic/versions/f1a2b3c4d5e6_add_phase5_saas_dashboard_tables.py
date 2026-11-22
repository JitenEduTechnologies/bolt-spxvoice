"""add phase5 saas dashboard tables

Revision ID: f1a2b3c4d5e6
Revises: b7c4d8e9f012
Create Date: 2026-11-04 10:40:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = 'b7c4d8e9f012'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # 1. dashboard_events
    if "dashboard_events" not in existing_tables:
        op.create_table(
            'dashboard_events',
            sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('event_type', sa.String(length=64), nullable=False),
            sa.Column('resource_type', sa.String(length=64), nullable=False),
            sa.Column('resource_id', sa.String(length=128), nullable=False),
            sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        )
        op.create_index('ix_dashboard_events_id', 'dashboard_events', ['id'])
        op.create_index('ix_dashboard_events_organization_id', 'dashboard_events', ['organization_id'])
        op.create_index('ix_dashboard_events_event_type', 'dashboard_events', ['event_type'])
        op.create_index('idx_dashboard_events_org_id', 'dashboard_events', ['organization_id', 'id'])

    # 2. chatwoot_outbox_jobs
    if "chatwoot_outbox_jobs" not in existing_tables:
        op.create_table(
            'chatwoot_outbox_jobs',
            sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('workflow_run_id', sa.Integer(), sa.ForeignKey('workflow_runs.id', ondelete='SET NULL'), nullable=True),
            sa.Column('message_type', sa.String(length=64), nullable=False),
            sa.Column('recipient_phone', sa.String(length=32), nullable=False),
            sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
            sa.Column('status', sa.String(length=32), server_default=sa.text("'QUEUED'"), nullable=False),
            sa.Column('idempotency_key', sa.String(length=128), nullable=False),
            sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
            sa.Column('last_error', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
            sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index('ix_chatwoot_outbox_jobs_id', 'chatwoot_outbox_jobs', ['id'])
        op.create_index('ix_chatwoot_outbox_jobs_organization_id', 'chatwoot_outbox_jobs', ['organization_id'])
        op.create_index('ix_chatwoot_outbox_jobs_idempotency_key', 'chatwoot_outbox_jobs', ['idempotency_key'], unique=True)
        op.create_index('idx_chatwoot_outbox_org_status', 'chatwoot_outbox_jobs', ['organization_id', 'status'])

    # 3. objection_responses
    if "objection_responses" not in existing_tables:
        op.create_table(
            'objection_responses',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('objection_category', sa.String(length=64), nullable=False),
            sa.Column('objection_text', sa.String(length=255), nullable=False),
            sa.Column('draft_response', sa.Text(), nullable=False),
            sa.Column('approved_response', sa.Text(), nullable=True),
            sa.Column('status', sa.String(length=32), server_default=sa.text("'DRAFT'"), nullable=False),
            sa.Column('version_number', sa.Integer(), server_default=sa.text('1'), nullable=False),
            sa.Column('approved_by_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        )
        op.create_index('ix_objection_responses_id', 'objection_responses', ['id'])
        op.create_index('ix_objection_responses_organization_id', 'objection_responses', ['organization_id'])
        op.create_index('idx_objection_responses_org_status', 'objection_responses', ['organization_id', 'status'])

    # 4. customer_profiles
    if "customer_profiles" not in existing_tables:
        op.create_table(
            'customer_profiles',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('phone_number', sa.String(length=32), nullable=False),
            sa.Column('name', sa.String(length=128), nullable=True),
            sa.Column('email', sa.String(length=128), nullable=True),
            sa.Column('channel', sa.String(length=32), server_default=sa.text("'telephony'"), nullable=False),
            sa.Column('interaction_count', sa.Integer(), server_default=sa.text('1'), nullable=False),
            sa.Column('last_lead_status', sa.String(length=32), nullable=True),
            sa.Column('custom_attributes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
            sa.Column('external_identifiers', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
            sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
            sa.UniqueConstraint('organization_id', 'phone_number', name='uq_customer_profiles_org_phone'),
        )
        op.create_index('ix_customer_profiles_id', 'customer_profiles', ['id'])
        op.create_index('ix_customer_profiles_organization_id', 'customer_profiles', ['organization_id'])
        op.create_index('idx_customer_profiles_org_phone', 'customer_profiles', ['organization_id', 'phone_number'])


def downgrade():
    op.drop_table('customer_profiles')
    op.drop_table('objection_responses')
    op.drop_table('chatwoot_outbox_jobs')
    op.drop_table('dashboard_events')

