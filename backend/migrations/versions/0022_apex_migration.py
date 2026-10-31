"""apex_migration: Oracle APEX projects, analysis of their application exports

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    op.create_table(
        'apex_migration',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('job_id', sa.Uuid(), nullable=True),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('target', sa.String(length=10), nullable=False),
        sa.Column('plan_mode', sa.String(length=10), nullable=False),
        sa.Column('summary', JSON, nullable=False),
        sa.Column('apps', JSON, nullable=False),
        sa.Column('plan', JSON, nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_apex_migration_project_id', 'apex_migration', ['project_id'])


def downgrade():
    op.drop_index('ix_apex_migration_project_id', table_name='apex_migration')
    op.drop_table('apex_migration')
