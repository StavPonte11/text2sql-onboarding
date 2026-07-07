"""add large category values table

Revision ID: ed40dd0a57ad
Revises: f9a3d1c8e205
Create Date: 2026-07-05 16:42:29.737642

"""
from typing import Sequence, Union
import pgvector

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ed40dd0a57ad'
down_revision: Union[str, None] = 'f9a3d1c8e205'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Enable the vector extension 
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Table generation 
    op.create_table('large_category_values',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('table_id', sa.String(), nullable=False),
        sa.Column('column_name', sa.String(), nullable=False),
        sa.Column('value_text', sa.String(), nullable=False),
        sa.Column('embedding', pgvector.sqlalchemy.VECTOR(dim=768), nullable=True),
        sa.Column('embedder_model', sa.String(), nullable=False, server_default="nomic-embed-text"),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['table_id'], ['tables.id'], onupdate='CASCADE', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('table_id', 'column_name', 'value_text', name='uq_large_category_val')
    )
    
    # 3. Fast-lookup indexing
    op.create_index(op.f('ix_large_category_values_column_name'), 'large_category_values', ['column_name'], unique=False)
    op.create_index(op.f('ix_large_category_values_table_id'), 'large_category_values', ['table_id'], unique=False)
    op.create_index(op.f('ix_large_category_values_value_text'), 'large_category_values', ['value_text'], unique=False)


def downgrade() -> None:
    # Cleaned rollbacks
    op.drop_index(op.f('ix_large_category_values_value_text'), table_name='large_category_values')
    op.drop_index(op.f('ix_large_category_values_table_id'), table_name='large_category_values')
    op.drop_index(op.f('ix_large_category_values_column_name'), table_name='large_category_values')
    op.drop_table('large_category_values')