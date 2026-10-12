"""validation_status_width: room for PASS_WITH_STUBS / NOT_EXECUTABLE (code-review 45)

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('validation', schema=None) as batch_op:
        batch_op.alter_column('status', existing_type=sa.String(length=10), type_=sa.String(length=20), existing_nullable=False)


def downgrade():
    with op.batch_alter_table('validation', schema=None) as batch_op:
        batch_op.alter_column('status', existing_type=sa.String(length=20), type_=sa.String(length=10), existing_nullable=False)
