"""datastage_reconciliation: reconciliation evidence of a DataStage migration (uploads and their evaluation)

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('datastage_migration', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reconciliation', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
                                      nullable=False, server_default='{}'))


def downgrade():
    with op.batch_alter_table('datastage_migration', schema=None) as batch_op:
        batch_op.drop_column('reconciliation')
