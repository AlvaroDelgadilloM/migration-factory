"""datastage_action_state: progress of the DataStage manual-action worklist

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('datastage_migration', schema=None) as batch_op:
        batch_op.add_column(sa.Column('action_state', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
                                      nullable=False, server_default='{}'))


def downgrade():
    with op.batch_alter_table('datastage_migration', schema=None) as batch_op:
        batch_op.drop_column('action_state')
