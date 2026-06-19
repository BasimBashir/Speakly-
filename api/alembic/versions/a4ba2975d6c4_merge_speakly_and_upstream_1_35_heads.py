"""merge speakly and upstream 1.35 heads

Revision ID: a4ba2975d6c4
Revises: 384be6596b36, a1b2c3d4e5f7
Create Date: 2026-06-19 08:07:15.416137

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4ba2975d6c4'
down_revision: Union[str, None] = ('384be6596b36', 'a1b2c3d4e5f7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
