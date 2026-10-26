"""엔진 입출력 모델. JSON 키는 camelCase(앱·TS 포트·픽스처와 공유), 시각은 "HH:MM", 시간은 분(정수)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

PlanMode = Literal["fast", "balance", "cheap"]
Transport = Literal["transit", "car"]
LegMode = Literal["walk", "transit", "taxi", "car"]
Issue = Literal["late", "open", "close"]
Pace = Literal["relaxed", "normal", "packed"]

START = "@start"  # 하루 시작 지점(숙소) id
END = "@end"  # 하루 끝 지점(숙소) id


class EngineModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


class Point(EngineModel):
    lat: float
    lon: float


class Place(EngineModel):
    """그날 기준으로 정리된 장소. 영업시간은 호출하는 쪽이 그 날짜의 값으로 넣는다."""

    id: str
    name: str
    type: str
    lat: float
    lon: float
    default_stay_min: int
    rating: float = 0
    cost_per_person: int = 0
    open: str | None = None  # None이면 제한 없음
    close: str | None = None
    last_entry: str | None = None
    closed: bool = False  # 그날 휴무
    indoor: bool = False
    parking_fee: int = 0
    lines: tuple[str, ...] = ()  # 가까운 역의 노선 id (근사 경로용)


class Item(EngineModel):
    place_id: str
    stay_min: int | None = None  # None이면 장소 기본 체류
    fixed_at: str | None = None  # 고정 예약 시각


class Leg(EngineModel):
    """알려진 구간(OTP·leg_cache). 없으면 엔진이 근사한다(§5.3)."""

    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    plan_mode: PlanMode
    mode: LegMode
    minutes: int
    fare: int
    km: float
    estimated: bool = False
    label: str = ""
    transfers: int = 0


class Delay(EngineModel):
    gap: int  # 지연이 생긴 구간: gap번째 장소로 들어가는 구간
    minutes: int


class FareBand(EngineModel):
    max_km: float
    fare: int


class TaxiFare(EngineModel):
    base: int
    base_km: float
    per_km: int


class City(EngineModel):
    line_links: tuple[tuple[str, str], ...] = ()  # 환승할 수 있는 노선 쌍
    fare_bands: tuple[FareBand, ...] = ()  # 대중교통 근사 운임(경로 km 구간)
    taxi: TaxiFare = TaxiFare(base=500, base_km=1.0, per_km=400)
    fuel_per_km: float = 0


class Day(EngineModel):
    date: str
    start: str
    items: tuple[Item, ...]
    places: dict[str, Place]
    city: City = City()
    plan_mode: PlanMode = "balance"
    transport: Transport = "transit"
    legs: tuple[Leg, ...] = ()
    delay: Delay | None = None
    start_point: Point | None = None
    end_point: Point | None = None
    day_end: str | None = None  # None이면 상수 dayEnd


class StopRow(EngineModel):
    kind: Literal["stop"] = "stop"
    place_id: str
    name: str
    arrive: str
    wait: int
    start: str
    end: str
    stay_min: int
    issue: Issue | None = None
    fixed_at: str | None = None


class LegRow(EngineModel):
    kind: Literal["leg"] = "leg"
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    mode: LegMode
    minutes: int
    fare: int
    km: float
    estimated: bool
    transfers: int = 0
    label: str = ""
    delay_min: int = 0
    break_min: int = 0  # 자동차 연속 운전 휴식


class Totals(EngineModel):
    move_min: int
    fare_per_person: int
    spend_per_person: int
    end: str
    issues: int
    over_end: bool


class DayResult(EngineModel):
    rows: tuple[StopRow | LegRow, ...]
    totals: Totals
