"""http_test_wave: an automatic test run of one deployment wave of a tomcat-war migration

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('http_test_run', schema=None) as batch_op:
        batch_op.add_column(sa.Column('wave', sa.String(length=40), nullable=True))


def downgrade():
    with op.batch_alter_table('http_test_run', schema=None) as batch_op:
        batch_op.drop_column('wave')
