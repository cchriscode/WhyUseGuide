"""PostgreSQL + PostGIS 스키마 (PRD §6, §13.2). 테이블은 단계마다 필요한 것부터 더한다."""

import enum
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampedTable(Base):
    """모든 테이블 공통: id(UUID), created_at, updated_at."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DataTier(enum.StrEnum):
    precise = "precise"
    basic = "basic"
    reference = "reference"


class NameSource(enum.StrEnum):
    """name_ko의 출처. machine이면 앱이 "기계 번역"으로 표시한다(ODPT 개발자 가이드라인 5)."""

    official = "official"
    editor = "editor"
    machine = "machine"


class CityKind(enum.StrEnum):
    city = "city"
    region = "region"


class Country(TimestampedTable):
    __tablename__ = "countries"

    code: Mapped[str] = mapped_column(String(2), unique=True)
    name_ko: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3))
    default_timezone: Mapped[str] = mapped_column(Text)
    languages: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    entry_info_url: Mapped[str | None] = mapped_column(Text)


class City(TimestampedTable):
    __tablename__ = "cities"

    code: Mapped[str] = mapped_column(Text, unique=True)
    country_code: Mapped[str] = mapped_column(ForeignKey("countries.code"))
    kind: Mapped[CityKind] = mapped_column(Enum(CityKind, name="city_kind"), server_default="city")
    name_ko: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3))
    fare_table: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    launched: Mapped[bool] = mapped_column(Boolean, server_default="false")
    move_cap_factor: Mapped[Decimal] = mapped_column(Numeric(3, 2), server_default="1.00")
    data_tier: Mapped[DataTier] = mapped_column(Enum(DataTier, name="data_tier"), server_default="reference")
    tier_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    airport_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")


class Line(TimestampedTable):
    """노선. source_id는 data/pipelines/sources.toml의 출처 id, estimated는 근사 노선("예상")."""

    __tablename__ = "lines"
    __table_args__ = (UniqueConstraint("source_id", "gtfs_route_id"),)

    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cities.id"))
    source_id: Mapped[str] = mapped_column(Text)
    gtfs_route_id: Mapped[str] = mapped_column(Text)
    name_ko: Mapped[str | None] = mapped_column(Text)
    name_ko_source: Mapped[NameSource | None] = mapped_column(Enum(NameSource, name="name_source"))
    # 검색 별칭: 관용 표기(츠키지, 쿠라마에 등). 표시는 name_ko(표기법)를 쓴다.
    name_ko_aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    name_local: Mapped[str] = mapped_column(Text)
    name_en: Mapped[str | None] = mapped_column(Text)
    color_hex: Mapped[str | None] = mapped_column(String(7))
    operator: Mapped[str] = mapped_column(Text)
    estimated: Mapped[bool] = mapped_column(Boolean, server_default="false")


class Stop(TimestampedTable):
    __tablename__ = "stops"
    __table_args__ = (
        UniqueConstraint("source_id", "gtfs_stop_id"),
        Index("ix_stops_location", "location", postgresql_using="gist"),
    )

    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cities.id"))
    source_id: Mapped[str] = mapped_column(Text)
    gtfs_stop_id: Mapped[str] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(Text)
    name_ko: Mapped[str | None] = mapped_column(Text)
    name_ko_source: Mapped[NameSource | None] = mapped_column(Enum(NameSource, name="name_source"))
    # 검색 별칭: 관용 표기(츠키지, 쿠라마에 등). 표시는 name_ko(표기법)를 쓴다.
    name_ko_aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    name_local: Mapped[str] = mapped_column(Text)
    name_en: Mapped[str | None] = mapped_column(Text)
    location: Mapped[WKBElement] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    parent_station_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stops.id", ondelete="SET NULL"))


class StopLine(TimestampedTable):
    __tablename__ = "stop_lines"
    __table_args__ = (UniqueConstraint("stop_id", "line_id"),)

    stop_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stops.id", ondelete="CASCADE"))
    line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lines.id", ondelete="CASCADE"))


class PlaceStatus(enum.StrEnum):
    active = "active"
    closed = "closed"
    hidden = "hidden"  # 검수 전 후보 또는 숨김


class TagSource(enum.StrEnum):
    llm = "llm"
    editor = "editor"
    user = "user"


class Place(TimestampedTable):
    """장소 (PRD §6). type은 PRD 유형 목록(신사·사찰, 박물관, 시장, …)의 한국어 값.

    source·source_ref: 어디서 온 후보인지(예: wikidata + Q-ID). verified_at이 있으면 편집자가 검수한 것이고,
    출처 데이터를 다시 받아도 덮어쓰지 않는다.
    """

    __tablename__ = "places"
    __table_args__ = (
        UniqueConstraint("source", "source_ref"),
        Index("ix_places_location", "location", postgresql_using="gist"),
    )

    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cities.id"))
    name_ko: Mapped[str | None] = mapped_column(Text)
    name_local: Mapped[str] = mapped_column(Text)
    name_en: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    indoor: Mapped[bool] = mapped_column(Boolean, server_default="false")
    popularity: Mapped[int] = mapped_column(SmallInteger, server_default="3")  # 1 로컬 ~ 5 유명
    rating: Mapped[Decimal | None] = mapped_column(Numeric(2, 1))
    review_count: Mapped[int] = mapped_column(Integer, server_default="0")
    default_stay_min: Mapped[int] = mapped_column(Integer, server_default="60")
    cost_per_person: Mapped[int] = mapped_column(Integer, server_default="0")
    currency: Mapped[str] = mapped_column(String(3))
    location: Mapped[WKBElement] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    nearest_stop_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stops.id", ondelete="SET NULL"))
    bookable: Mapped[bool] = mapped_column(Boolean, server_default="false")
    affiliate_url: Mapped[str | None] = mapped_column(Text)
    tip_ko: Mapped[str | None] = mapped_column(Text)
    parking_fee: Mapped[int | None] = mapped_column(Integer)
    website: Mapped[str | None] = mapped_column(Text)
    status: Mapped[PlaceStatus] = mapped_column(
        Enum(PlaceStatus, name="place_status"), server_default="hidden"
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str] = mapped_column(Text)


class PlaceHours(TimestampedTable):
    """요일별 영업시간. 휴무 요일은 행이 없다. weekday 0 = 월요일."""

    __tablename__ = "place_hours"
    __table_args__ = (UniqueConstraint("place_id", "weekday", "open_time"),)

    place_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column(SmallInteger)
    open_time: Mapped[time] = mapped_column(Time)
    close_time: Mapped[time] = mapped_column(Time)
    last_entry_time: Mapped[time | None] = mapped_column(Time)


class PlaceClosure(TimestampedTable):
    __tablename__ = "place_closures"
    __table_args__ = (UniqueConstraint("place_id", "date"),)

    place_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)


class PlaceTag(TimestampedTable):
    __tablename__ = "place_tags"
    __table_args__ = (UniqueConstraint("place_id", "tag", "source"),)

    place_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"))
    tag: Mapped[str] = mapped_column(Text)
    weight: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    source: Mapped[TagSource] = mapped_column(Enum(TagSource, name="tag_source"))


class User(TimestampedTable):
    """게스트도 사용자다(기기 임시 계정, F-30).

    로그인하면 email·auth_provider가 채워지고 is_guest가 꺼진다.
    """

    __tablename__ = "users"

    email: Mapped[str | None] = mapped_column(Text, unique=True)
    auth_provider: Mapped[str] = mapped_column(Text, server_default="guest")
    is_guest: Mapped[bool] = mapped_column(Boolean, server_default="true")
    locale: Mapped[str] = mapped_column(Text, server_default="ko")


class AuthToken(TimestampedTable):
    """Bearer 토큰. 원문은 저장하지 않고 SHA-256만 둔다. 만료 30일(PRD §10)."""

    __tablename__ = "auth_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Trip(TimestampedTable):
    __tablename__ = "trips"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cities.id"))
    trip_style: Mapped[str] = mapped_column(Text, server_default="city")
    start_date: Mapped[date] = mapped_column(Date)
    days: Mapped[int] = mapped_column(SmallInteger)
    adults: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    children: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    companion: Mapped[str | None] = mapped_column(Text)
    tastes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    pace: Mapped[str] = mapped_column(Text, server_default="normal")
    budget: Mapped[str] = mapped_column(Text, server_default="normal")
    mix: Mapped[int] = mapped_column(SmallInteger, server_default="50")
    day_end_time: Mapped[time | None] = mapped_column(Time)
    plan_mode: Mapped[str | None] = mapped_column(Text)  # 3안 중 고른 이동 방식(고르기 전 NULL)
    prefs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")  # 평가로 바뀐 가중치(§5.6)


class TripFixedBooking(TimestampedTable):
    __tablename__ = "trip_fixed_bookings"

    trip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"))
    place_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("places.id"))
    date: Mapped[date] = mapped_column(Date)
    time: Mapped[time] = mapped_column(Time)


class TripDay(TimestampedTable):
    __tablename__ = "trip_days"
    __table_args__ = (UniqueConstraint("trip_id", "day_index"),)

    trip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"))
    day_index: Mapped[int] = mapped_column(SmallInteger)
    date: Mapped[date] = mapped_column(Date)
    start_time: Mapped[time] = mapped_column(Time)
    plan_mode: Mapped[str] = mapped_column(Text, server_default="balance")  # 그날 이동 방식(S-03 칩)
    cur_index: Mapped[int] = mapped_column(SmallInteger, server_default="-1")  # 지금 있는 장소, 출발 전 -1
    delay_gap: Mapped[int | None] = mapped_column(SmallInteger)
    delay_min: Mapped[int | None] = mapped_column(SmallInteger)
    kind: Mapped[str] = mapped_column(Text, server_default="normal")
    start_location: Mapped[WKBElement | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    end_location: Mapped[WKBElement | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    version: Mapped[int] = mapped_column(Integer, server_default="1")  # 동시 편집 충돌(409) 확인


class ItineraryItem(TimestampedTable):
    __tablename__ = "itinerary_items"
    __table_args__ = (UniqueConstraint("trip_day_id", "position"),)

    trip_day_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trip_days.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(SmallInteger)
    place_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("places.id"))
    stay_min: Mapped[int | None] = mapped_column(SmallInteger)  # NULL이면 장소 기본 체류
    fixed_at: Mapped[time | None] = mapped_column(Time)  # 고정 예약 시각
    status: Mapped[str] = mapped_column(Text, server_default="planned")  # planned/done/skipped


class ItinerarySnapshot(TimestampedTable):
    """되돌리기·분석용 일정 사본. reason: generate(처음 생성, 이동 방식별) / user_edit / auto_adjust."""

    __tablename__ = "itinerary_snapshots"

    trip_day_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trip_days.id", ondelete="CASCADE"))
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    plan_mode: Mapped[str | None] = mapped_column(Text)
    messages: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")


class CourseRating(TimestampedTable):
    __tablename__ = "course_ratings"

    trip_day_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trip_days.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    stars: Mapped[int] = mapped_column(SmallInteger)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    route_accuracy: Mapped[str | None] = mapped_column(Text)  # ok/bit/off
    apply_next: Mapped[bool] = mapped_column(Boolean, server_default="true")
    share_anon: Mapped[bool] = mapped_column(Boolean, server_default="true")


class CoursePlaceVote(TimestampedTable):
    __tablename__ = "course_place_votes"
    __table_args__ = (UniqueConstraint("course_rating_id", "place_id"),)

    course_rating_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course_ratings.id", ondelete="CASCADE"))
    place_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("places.id"))
    vote: Mapped[int] = mapped_column(SmallInteger)  # +1 / -1
