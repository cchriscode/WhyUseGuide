"""우리가 직접 만드는 GTFS zip 쓰기 (근사 노선, ODPT 역 시간표 변환이 같이 쓴다).

운행일은 세 가지다: all(매일), weekday(월~금), holiday(토·일). 평일에 걸린 공휴일은 calendar_dates로
휴일 운행으로 옮긴다. 시각은 자정 기준 분(24시를 넘으면 1440 이상).
"""

import csv
import io
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SERVICE_DAYS = {"all": "1111111", "weekday": "1111100", "holiday": "0000011"}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
FARE_PAID_BEFORE_BOARDING = "1"  # GTFS payment_method: 타기 전에 낸다(개찰구)
AGENCY_URL = "https://example.invalid/"  # GTFS 필수 칸. 운영사 주소를 대신 적지 않는다(우리가 만든 파일이다).


@dataclass
class Trip:
    trip_id: str
    route_id: str
    service_name: str
    service_days: str  # SERVICE_DAYS의 키
    headsign: str
    stops: list[tuple[str, float]]  # (stop_id, 자정 기준 분). 도착 = 출발로 쓴다.


def hms(minutes: float) -> str:
    s = round(minutes * 60)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def csv_text(rows: Iterable[Mapping[str, str]], fields: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def write_feed(
    out: Path,
    *,
    agencies: Mapping[str, str],
    stops: Iterable[Mapping[str, str]],
    routes: Iterable[Mapping[str, str]],
    trips: list[Trip],
    translations: Iterable[Mapping[str, str]],
    valid_from: date,
    valid_to: date,
    timezone: str,
    lang: str,
    publisher: str,
    currency: str = "JPY",
    holidays: set[date] | None = None,
    fares: Iterable[tuple[str, str, int]] | None = None,
) -> None:
    """GTFS zip을 쓴다. agencies = {agency_id: 이름}, stops·routes·translations = GTFS 행.

    fares = [(출발 정류장, 도착 정류장, 운임)]. 주면 정류장마다 요금 구역(zone_id = stop_id)을 두고
    구역 쌍별 운임(fare_attributes·fare_rules)을 쓴다. OTP가 이 표로 구간 요금을 계산한다.
    """
    stop_rows = [dict(s) for s in stops]
    stop_fields = ["stop_id", "stop_name", "stop_lat", "stop_lon"]
    fare_files: dict[str, str] = {}
    if fares is not None:
        fare_list = sorted(set(fares))
        agency_id = next(iter(agencies))
        stop_rows = [s | {"zone_id": s["stop_id"]} for s in stop_rows]
        stop_fields.append("zone_id")
        fare_files = {
            "fare_attributes.txt": csv_text(
                [
                    {
                        "fare_id": str(price),
                        "price": str(price),
                        "currency_type": currency,
                        "payment_method": FARE_PAID_BEFORE_BOARDING,
                        "transfers": "",
                        "agency_id": agency_id,
                    }
                    for price in sorted({price for _, _, price in fare_list})
                ],
                ["fare_id", "price", "currency_type", "payment_method", "transfers", "agency_id"],
            ),
            "fare_rules.txt": csv_text(
                [{"fare_id": str(price), "origin_id": a, "destination_id": b} for a, b, price in fare_list],
                ["fare_id", "origin_id", "destination_id"],
            ),
        }
    # 평일(월~금)에 걸린 공휴일만 예외로 둔다. 토·일 공휴일은 원래 휴일 운행이다.
    weekday_holidays = {d for d in (holidays or set()) if valid_from <= d <= valid_to and d.weekday() < 5}
    files = {
        "agency.txt": csv_text(
            [
                {"agency_id": a, "agency_name": name, "agency_url": AGENCY_URL, "agency_timezone": timezone}
                for a, name in agencies.items()
            ],
            ["agency_id", "agency_name", "agency_url", "agency_timezone"],
        ),
        "stops.txt": csv_text(stop_rows, stop_fields),
        "routes.txt": csv_text(
            routes, ["route_id", "agency_id", "route_long_name", "route_type", "route_color"]
        ),
        "calendar.txt": csv_text(
            [
                {
                    "service_id": sid,
                    **dict(zip(WEEKDAYS, pattern, strict=True)),
                    "start_date": valid_from.strftime("%Y%m%d"),
                    "end_date": valid_to.strftime("%Y%m%d"),
                }
                for sid, pattern in SERVICE_DAYS.items()
            ],
            ["service_id", *WEEKDAYS, "start_date", "end_date"],
        ),
        "calendar_dates.txt": csv_text(
            [
                {"service_id": sid, "date": d.strftime("%Y%m%d"), "exception_type": kind}
                for d in sorted(weekday_holidays)
                for sid, kind in (("weekday", "2"), ("holiday", "1"))
            ],
            ["service_id", "date", "exception_type"],
        ),
        "trips.txt": csv_text(
            [
                {
                    "route_id": t.route_id,
                    "service_id": t.service_days,
                    "trip_id": t.trip_id,
                    "trip_headsign": t.headsign,
                    "trip_short_name": t.service_name,
                }
                for t in trips
            ],
            ["route_id", "service_id", "trip_id", "trip_headsign", "trip_short_name"],
        ),
        "stop_times.txt": csv_text(
            [
                {
                    "trip_id": t.trip_id,
                    "arrival_time": hms(m),
                    "departure_time": hms(m),
                    "stop_id": sid,
                    "stop_sequence": str(i + 1),
                }
                for t in trips
                for i, (sid, m) in enumerate(t.stops)
            ],
            ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
        ),
        "translations.txt": csv_text(
            translations, ["table_name", "field_name", "language", "translation", "record_id"]
        ),
        "feed_info.txt": csv_text(
            [
                {
                    "feed_publisher_name": publisher,
                    "feed_publisher_url": AGENCY_URL,
                    "feed_lang": lang,
                    "feed_start_date": valid_from.strftime("%Y%m%d"),
                    "feed_end_date": valid_to.strftime("%Y%m%d"),
                }
            ],
            ["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date", "feed_end_date"],
        ),
    }
    files |= fare_files
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in files.items():
            zf.writestr(name, body)
