"""empty start

Revision ID: 983f40305cfa
Revises:
Create Date: 2026-11-12 09:40:36.188609
"""

from collections.abc import Sequence

revision: str = "983f40305cfa"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
