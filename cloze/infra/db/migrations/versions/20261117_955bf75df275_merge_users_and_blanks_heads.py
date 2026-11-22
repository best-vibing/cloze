"""merge users and blanks heads

Revision ID: 955bf75df275
Revises: 31c008eae16b, 89e789a9a533
Create Date: 2026-11-17 19:49:34.508494
"""

from collections.abc import Sequence

revision: str = "955bf75df275"
down_revision: str | Sequence[str] | None = ("31c008eae16b", "89e789a9a533")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
