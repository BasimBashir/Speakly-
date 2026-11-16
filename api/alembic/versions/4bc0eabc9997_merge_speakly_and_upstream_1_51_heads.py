"""merge speakly and upstream 1.51 heads

Revision ID: 4bc0eabc9997
Revises: a4ba2975d6c4, d6a83e2b19f0
Create Date: 2026-11-16 16:08:36.659575

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4bc0eabc9997'
down_revision: Union[str, None] = ('a4ba2975d6c4', 'd6a83e2b19f0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
