"""PostgreSQL + PostGIS 스키마 (PRD §6, §13.2). 테이블은 단계마다 필요한 것부터 더한다."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
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
