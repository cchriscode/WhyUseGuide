"""seed japan and tokyo

출시 1차 도시(PRD §0). 등급(data_tier)은 배치가 기준을 계산하고 운영자가 승인하므로(§13.1)
처음에는 reference, 출시 전까지 launched=false로 둔다.

Revision ID: 29919fc56c48
Revises: eb0e6e4d1eef
Create Date: 2026-10-26 13:55:00

"""

from collections.abc import Sequence

from alembic import op

revision: str = "29919fc56c48"
down_revision: str | Sequence[str] | None = "eb0e6e4d1eef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO countries (code, name_ko, currency, default_timezone, languages)
        VALUES ('JP', '일본', 'JPY', 'Asia/Tokyo', '{ja}');
        INSERT INTO cities (code, country_code, kind, name_ko, timezone, currency, launched,
                            data_tier, airport_codes)
        VALUES ('tokyo', 'JP', 'city', '도쿄', 'Asia/Tokyo', 'JPY', false, 'reference', '{HND,NRT}');
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM cities WHERE code = 'tokyo'; DELETE FROM countries WHERE code = 'JP';")
