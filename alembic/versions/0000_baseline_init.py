"""0000_baseline_init

Revision ID: 0000_baseline_init
Revises: 
Create Date: 2026-10-13 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0000_baseline_init'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Baseline migration ensuring alembic_version table and migration engine are operational
    pass


def downgrade() -> None:
    pass
