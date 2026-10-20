"""plan_java_version: the Java version chosen for a plan (mf.versions); NULL = the profile's own

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('plan', schema=None) as batch_op:
        batch_op.add_column(sa.Column('java_version', sa.String(length=10), nullable=True))


def downgrade():
    with op.batch_alter_table('plan', schema=None) as batch_op:
        batch_op.drop_column('java_version')
