"""course_ratings one per day and user

Revision ID: 7ea126134573
Revises: 01842c4cfe25
Create Date: 2026-11-16 20:46:50.762743

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7ea126134573"
down_revision: str | Sequence[str] | None = "01842c4cfe25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# PostgreSQL이 이름 없는 유일 제약에 붙이는 이름과 같은 형식(다른 테이블의 제약과 맞춘다)
CONSTRAINT = "course_ratings_trip_day_id_user_id_key"


def upgrade() -> None:
    """하루·사용자당 평가 하나. 이미 여러 번 들어간 평가는 가장 먼저 낸 것만 남긴다(투표는 CASCADE)."""
    op.execute(
        """
        DELETE FROM course_ratings r USING course_ratings first
        WHERE r.trip_day_id = first.trip_day_id AND r.user_id = first.user_id
          AND (first.created_at, first.id) < (r.created_at, r.id)
        """
    )
    op.create_unique_constraint(CONSTRAINT, "course_ratings", ["trip_day_id", "user_id"])


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "course_ratings", type_="unique")
