"""http_test_performance: options of the comparative load test of an automatic test run

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


def upgrade():
    with op.batch_alter_table('http_test_run', schema=None) as batch_op:
        batch_op.add_column(sa.Column('performance', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
                                      nullable=True))


def downgrade():
    with op.batch_alter_table('http_test_run', schema=None) as batch_op:
        batch_op.drop_column('performance')
