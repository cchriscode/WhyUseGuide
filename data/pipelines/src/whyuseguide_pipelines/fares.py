"""도시별 근사 운임 적재 (city_fares.toml → cities.fare_table).

fare_table의 모양은 엔진의 Fares 모델(itinerary_engine.models)이다. 여기서 그 모델로 만들어 넣으므로
DB에는 엔진이 읽을 수 있는 값만 들어간다.
"""

import tomllib
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, update

from itinerary_engine.models import FareBand, Fares, TaxiFare
from whyuseguide_api.db.models import City

M_PER_KM = 1000


def fares_from(entry: dict[str, Any]) -> Fares:
    """city_fares.toml의 도시 항목 → 엔진 운임. 빠진 값이 있으면 KeyError로 멈춘다."""
    taxi, car = entry["taxi"], entry["car"]
    return Fares(
        fare_bands=tuple(FareBand(max_km=km, fare=fare) for km, fare in entry["transit"]["bands"]),
        taxi=TaxiFare(
            base=taxi["base"],
            base_km=taxi["base_km"],
            per_km=round(taxi["add_fare"] / taxi["add_m"] * M_PER_KM),
        ),
        fuel_per_km=round(car["gasoline_per_liter"] / car["km_per_liter"], 1),
    )


def read_city_fares(path: Path) -> dict[str, Fares]:
    with path.open("rb") as f:
        return {code: fares_from(entry) for code, entry in tomllib.load(f).items()}


def load_fares(conn: Connection, fares: dict[str, Fares]) -> list[str]:
    """도시 코드별 운임을 cities.fare_table에 넣는다. 반환: DB에 없는 도시 코드(넣지 못한 것)."""
    missing = []
    for code, fare in fares.items():
        result = conn.execute(
            update(City)
            .where(City.code == code)
            .values(fare_table=fare.model_dump(by_alias=True, mode="json"))
        )
        if result.rowcount == 0:
            missing.append(code)
    return missing
