"""odoo_flow_suite: the business flows (Pruebas) of an Odoo project

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'odoo_flow_suite',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('updated_by', sa.String(length=255), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id'),
    )


def downgrade():
    op.drop_table('odoo_flow_suite')
