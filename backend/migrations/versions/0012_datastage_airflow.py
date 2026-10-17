"""datastage_airflow: project.migration_type + datastage_migration (DataStage -> Airflow adapter)

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    with op.batch_alter_table('project', schema=None) as batch_op:
        batch_op.add_column(sa.Column('migration_type', sa.String(length=30), nullable=False, server_default='JAVA_CAMEL'))
    op.create_table(
        'datastage_migration',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('project_id', sa.Uuid(), sa.ForeignKey('project.id', ondelete='CASCADE'), nullable=False),
        sa.Column('job_id', sa.Uuid(), nullable=True),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('options', JSON, nullable=False),
        sa.Column('summary', JSON, nullable=False),
        sa.Column('results', JSON, nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_datastage_migration_project_id', 'datastage_migration', ['project_id'])


def downgrade():
    op.drop_index('ix_datastage_migration_project_id', 'datastage_migration')
    op.drop_table('datastage_migration')
    with op.batch_alter_table('project', schema=None) as batch_op:
        batch_op.drop_column('migration_type')
