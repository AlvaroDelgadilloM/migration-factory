"""workspace_migration_target: tomcat-war workspace migrations on the platform (no TargetProfile)

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('workspace_migration', schema=None) as batch_op:
        batch_op.add_column(sa.Column('target', sa.String(length=20), nullable=False, server_default='profile'))
        batch_op.alter_column('profile_id', existing_type=sa.Uuid(), nullable=True)


def downgrade():
    with op.batch_alter_table('workspace_migration', schema=None) as batch_op:
        batch_op.alter_column('profile_id', existing_type=sa.Uuid(), nullable=False)
        batch_op.drop_column('target')
