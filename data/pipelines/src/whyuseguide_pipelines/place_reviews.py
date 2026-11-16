"""장소 검수 자료 적재 (place_reviews/<도시>.json → places, place_hours).

검수 자료는 후보 장소마다 "지금 갈 수 있는가, 언제 여는가, 얼마인가"를 공식 자료에서 확인해 출처와 함께 적은
것이다. 여기서 그 자료를 읽어 장소 정보를 채우고 공개 여부를 정한다.

공개(active) 기준: 운영 중이고(open), 확인 수준이 high·medium이고, 여는 시간을 알고, 따로 뺀 사유가 없을 것.
문을 닫은 곳은 closed, 나머지(임시 휴관, 이전, 도쿄 밖, 장소가 아님, 확인 부족)는 hidden으로 둔다.
검수한 장소는 verified_at이 채워져서, 출처 데이터를 다시 받아도 덮어쓰지 않는다(places.load_places).
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Connection, delete, insert, select, update

from itinerary_engine.config import constants
from whyuseguide_api.db.models import City, Place, PlaceHours, PlaceStatus

Verdict = Literal[
    "open",
    "temporarily_closed",
    "closed_permanently",
    "moved",
    "not_a_visitor_place",
    "outside_tokyo",
    "duplicate",
    "unknown",
]
Confidence = Literal["high", "medium", "low"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[Weekday, ...] = (
    "mon",
    "tue",
    "wed",
    "thu",
    "fri",
    "sat",
    "sun",
)  # place_hours.weekday 0 = 월
HHMM = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HoursRule(Strict):
    days: list[Weekday] = Field(min_length=1)
    open: str = HHMM
    close: str = HHMM
    last_entry: str | None = HHMM


class PlaceReview(Strict):
    ref: str  # places.source_ref
    name_local: str
    verdict: Verdict
    name_ko: str | None = None
    name_ko_basis: Literal["official", "common", "transliteration"] | None = None
    type: str | None = None
    indoor: bool | None = None
    website: str | None = None
    hours: Literal["always"] | list[HoursRule] | None = None  # always = 언제든 들어갈 수 있다
    cost_per_person: int | None = Field(default=None, ge=0)
    default_stay_min: int | None = Field(default=None, gt=0)
    bookable: bool | None = None
    closures_note: str | None = None
    sources: list[str] = []
    confidence: Confidence
    notes: str | None = None
    excluded: str | None = None  # 운영 중이어도 추천에서 빼는 편집 판단(사유)

    @field_validator("type")
    @classmethod
    def known_type(cls, value: str | None) -> str | None:
        if value is not None and value not in constants()["placeTypes"]:
            raise ValueError(f"정의되지 않은 장소 유형: {value}")
        return value

    def status(self) -> PlaceStatus:
        if self.verdict == "closed_permanently":
            return PlaceStatus.closed
        confirmed = self.confidence in ("high", "medium") and self.hours is not None
        if self.verdict == "open" and confirmed and not self.excluded:
            return PlaceStatus.active
        return PlaceStatus.hidden


class ReviewFile(Strict):
    city: str  # cities.code
    source: str  # places.source (어느 후보 목록을 검수했는지)
    checked: date  # 출처와 대조한 날
    method: str  # 어떻게 확인했는지(사람, 조사 방식)
    reviews: list[PlaceReview]


def read_reviews(path: Path) -> ReviewFile:
    return ReviewFile.model_validate(json.loads(path.read_text(encoding="utf-8")))


@dataclass
class ReviewResult:
    by_status: dict[str, int] = field(default_factory=dict)
    hours_rows: int = 0
    missing: list[str] = field(default_factory=list)  # 검수 자료에는 있는데 DB에 없는 장소


def _time(hhmm: str) -> time:
    return time(int(hhmm[:2]), int(hhmm[3:]))


def apply_reviews(conn: Connection, data: ReviewFile) -> ReviewResult:
    """검수 자료를 장소에 반영한다. 자료에 값이 없는 칸은 건드리지 않는다.

    트랜잭션은 호출하는 쪽이 관리한다.
    """
    city_id = conn.execute(select(City.id).where(City.code == data.city)).scalar_one()
    ids = dict(
        conn.execute(
            select(Place.source_ref, Place.id).where(Place.city_id == city_id, Place.source == data.source)
        ).all()
    )
    verified_at = datetime(data.checked.year, data.checked.month, data.checked.day, tzinfo=UTC)
    result = ReviewResult()
    for review in data.reviews:
        place_id = ids.get(review.ref)
        if place_id is None:
            result.missing.append(review.ref)
            continue
        status = review.status()
        values = {
            "name_ko": review.name_ko,
            "type": review.type,
            "indoor": review.indoor,
            "website": review.website,
            "cost_per_person": review.cost_per_person,
            "default_stay_min": review.default_stay_min,
            "bookable": review.bookable,
        }
        conn.execute(
            update(Place)
            .where(Place.id == place_id)
            .values(
                {k: v for k, v in values.items() if v is not None}
                | {"status": status, "verified_at": verified_at}
            )
        )
        conn.execute(delete(PlaceHours).where(PlaceHours.place_id == place_id))
        if isinstance(review.hours, list):
            rows = [
                {
                    "place_id": place_id,
                    "weekday": WEEKDAYS.index(day),
                    "open_time": _time(rule.open),
                    "close_time": _time(rule.close),
                    "last_entry_time": _time(rule.last_entry) if rule.last_entry else None,
                }
                for rule in review.hours
                for day in rule.days
            ]
            conn.execute(insert(PlaceHours).values(rows))
            result.hours_rows += len(rows)
        result.by_status[status] = result.by_status.get(status, 0) + 1
    return result
