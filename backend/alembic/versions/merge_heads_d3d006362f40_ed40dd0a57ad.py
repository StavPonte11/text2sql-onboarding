"""merge heads d3d006362f40 and ed40dd0a57ad

Revision ID: merge_heads_d3d_ed40
Revises: d3d006362f40, ed40dd0a57ad
Create Date: 2026-07-21 11:54:00.000000

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'merge_heads_d3d_ed40'
down_revision: Union[str, Sequence[str], None] = ('d3d006362f40', 'ed40dd0a57ad')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
