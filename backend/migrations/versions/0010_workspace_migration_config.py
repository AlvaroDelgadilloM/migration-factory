"""workspace_migration_config: tomcat-war client file (groupId, containers, patches) from the platform

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('workspace_migration', schema=None) as batch_op:
        batch_op.add_column(sa.Column('config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True))


def downgrade():
    with op.batch_alter_table('workspace_migration', schema=None) as batch_op:
        batch_op.drop_column('config')
