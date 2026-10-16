"""http_test_run: automatic tests original vs migrated from the platform (Pruebas > Automáticas)

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    op.create_table(
        'http_test_run',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('job_id', sa.Uuid(), nullable=True),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('original', JSON, nullable=False),
        sa.Column('migrated', JSON, nullable=False),
        sa.Column('cases', JSON, nullable=False),
        sa.Column('screenshots', sa.Boolean(), nullable=False),
        sa.Column('summary', JSON, nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_http_test_run_project_id', 'http_test_run', ['project_id'])


def downgrade():
    op.drop_index('ix_http_test_run_project_id', table_name='http_test_run')
    op.drop_table('http_test_run')
