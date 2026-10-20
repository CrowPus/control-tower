"""The owner's own agents (made from the agent template)

Revision: 0002
Revises: 0001_core
"""
import sqlalchemy as sa
from alembic import op

import app.models.base


revision = '0002'
down_revision = '0001_core'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('owner_agents',
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('department', sa.String(length=40), nullable=False),
    sa.Column('job', sa.Text(), nullable=False),
    sa.Column('guide', sa.Text(), nullable=False),
    sa.Column('tools', sa.JSON(), nullable=False),
    sa.Column('actions', sa.JSON(), nullable=False),
    sa.Column('subscribes', sa.JSON(), nullable=False),
    sa.Column('emits', sa.JSON(), nullable=False),
    sa.Column('kpi', sa.String(length=60), nullable=False),
    sa.Column('max_rounds', sa.Integer(), nullable=False),
    sa.Column('template', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('created_by', sa.String(length=60), nullable=False),
    sa.Column('created_at', app.models.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.models.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )


def downgrade() -> None:
    op.drop_table('owner_agents')
