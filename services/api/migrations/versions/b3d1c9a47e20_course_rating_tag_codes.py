"""course_ratings.tags: 한국어 문구 → 코드값

Revision ID: b3d1c9a47e20
Revises: 7ea126134573
Create Date: 2026-11-16 21:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d1c9a47e20"
down_revision: str | Sequence[str] | None = "7ea126134573"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# S-09 태그 문구 ↔ 코드값(itinerary_engine.feedback.RatingTag). 마이그레이션은 그 시점의 값을 적어 둔다.
CODES = {
    "동선이 효율적": "efficient_route",
    "이동이 길었어요": "long_moves",
    "일정이 빡빡했어요": "too_tight",
    "여유로웠어요": "relaxed",
    "로컬 스팟 좋았어요": "local_good",
    "사람이 너무 많았어요": "crowded",
    "대기 줄이 길었어요": "long_queue",
    "시간 안내 정확": "accurate_times",
}


def _replace_all(mapping: dict[str, str]) -> None:
    stmt = sa.text("UPDATE course_ratings SET tags = array_replace(tags, :old, :new) WHERE :old = ANY(tags)")
    for old, new in mapping.items():
        op.get_bind().execute(stmt, {"old": old, "new": new})


def upgrade() -> None:
    _replace_all(CODES)


def downgrade() -> None:
    _replace_all({code: label for label, code in CODES.items()})
