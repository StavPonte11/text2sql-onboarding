"""add security users table

Revision ID: c3f1a9e72b84
Revises: ab82a8f25505
Create Date: 2026-05-25 14:44:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3f1a9e72b84'
down_revision: Union[str, None] = 'ab82a8f25505'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the 'security' schema if it doesn't exist
    op.execute("CREATE SCHEMA IF NOT EXISTS security")

    op.create_table(
        'users',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email', name='uq_security_users_email'),
        schema='security',
    )
    op.create_index(
        'ix_security_users_email',
        'users',
        ['email'],
        unique=True,
        schema='security',
    )


def downgrade() -> None:
    op.drop_index('ix_security_users_email', table_name='users', schema='security')
    op.drop_table('users', schema='security')
    # Note: we intentionally do NOT drop the schema itself to avoid
    # accidentally removing other objects that may already live there.
