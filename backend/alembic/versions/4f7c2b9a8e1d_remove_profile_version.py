"""remove_table_profile_version_and_make_table_id_unique

Revision ID: 4f7c2b9a8e1d
Revises: f4d69a17d156
Create Date: 2026-06-10 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text

# revision identifiers, used by Alembic.
revision = '4f7c2b9a8e1d'
down_revision = 'f4d69a17d156'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. Clean up duplicate table_profiles to keep only the latest per table
    # This keeps the profile with the max created_at for each table_id
    op.execute(
        """
        DELETE FROM table_profiles
        WHERE id NOT IN (
            SELECT id FROM (
                SELECT id,
                ROW_NUMBER() OVER (PARTITION BY table_id ORDER BY created_at DESC) as rn
                FROM table_profiles
            ) sub
            WHERE rn = 1
        )
        """
    )
    
    # 2. Add unique constraint to table_id
    op.create_unique_constraint('uq_table_profiles_table_id', 'table_profiles', ['table_id'])
    
    # 3. Drop version column
    op.drop_column('table_profiles', 'version')


def downgrade() -> None:
    op.add_column('table_profiles', sa.Column('version', sa.INTEGER(), autoincrement=False, nullable=True))
    op.drop_constraint('uq_table_profiles_table_id', 'table_profiles', type_='unique')
    
    # In downgrade we should probably just set version to 1
    op.execute("UPDATE table_profiles SET version = 1")
