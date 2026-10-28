"""odoo_migration: Odoo Community 12 -> 19 database migrations (analysis, decisions, hops, reconciliation)

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    op.create_table(
        'odoo_migration',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('project_id', sa.Uuid(), sa.ForeignKey('project.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('job_id', sa.Uuid(), nullable=True),
        sa.Column('mode', sa.String(10), nullable=False, server_default='analyze'),
        sa.Column('state', sa.String(20), nullable=False, server_default='queued'),
        sa.Column('analysis_id', sa.Uuid(), nullable=True),
        sa.Column('summary', JSON, nullable=False, server_default='{}'),
        sa.Column('modules', JSON, nullable=False, server_default='[]'),
        sa.Column('decisions', JSON, nullable=False, server_default='{}'),
        sa.Column('hops', JSON, nullable=False, server_default='[]'),
        sa.Column('reconciliation', JSON, nullable=False, server_default='[]'),
        sa.Column('created_by', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table('odoo_migration')
