"""http_test_run_from_migration: automatic tests that start the original and the migrated WAR themselves

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    with op.batch_alter_table('http_test_run', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source', sa.String(length=20), nullable=False, server_default='urls'))
        batch_op.add_column(sa.Column('workspace_migration_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('container', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('data_artifact_id', sa.Uuid(), nullable=True))
        batch_op.alter_column('original', existing_type=JSON, nullable=True)
        batch_op.alter_column('migrated', existing_type=JSON, nullable=True)


def downgrade():
    with op.batch_alter_table('http_test_run', schema=None) as batch_op:
        batch_op.alter_column('migrated', existing_type=JSON, nullable=False)
        batch_op.alter_column('original', existing_type=JSON, nullable=False)
        batch_op.drop_column('data_artifact_id')
        batch_op.drop_column('container')
        batch_op.drop_column('workspace_migration_id')
        batch_op.drop_column('source')
