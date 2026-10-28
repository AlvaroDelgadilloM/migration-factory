"""http_test_run_execution: automatic tests of a migrated project (Spring Boot, Tomcat WAR, Quarkus) started by the worker

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    with op.batch_alter_table('http_test_run', schema=None) as batch_op:
        batch_op.add_column(sa.Column('execution_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('variables', JSON, nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('params', JSON, nullable=False, server_default='{}'))


def downgrade():
    with op.batch_alter_table('http_test_run', schema=None) as batch_op:
        batch_op.drop_column('params')
        batch_op.drop_column('variables')
        batch_op.drop_column('execution_id')
