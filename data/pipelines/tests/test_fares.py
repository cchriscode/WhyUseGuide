from pathlib import Path

import pytest
from sqlalchemy import Connection, select
from support import conn as conn
from support import needs_db

from itinerary_engine.models import City as EngineCity
from itinerary_engine.models import Fares
from itinerary_engine.route import taxi_fare, transit_fare
from whyuseguide_api.db.models import City
from whyuseguide_pipelines.fares import fares_from, load_fares, read_city_fares
from whyuseguide_pipelines.sources import CITY_FARES

ENTRY = {
    "transit": {"bands": [[6, 180], [11, 210]]},
    "taxi": {"base": 500, "base_km": 1.0, "add_fare": 100, "add_m": 232},
    "car": {"gasoline_per_liter": 169.9, "km_per_liter": 14},
}


def test_fares_from_entry() -> None:
    fares = fares_from(ENTRY)
    assert [(b.max_km, b.fare) for b in fares.fare_bands] == [(6, 180), (11, 210)]
    assert fares.taxi is not None and fares.taxi.per_km == 431  # 232m마다 100엔 → km당 431엔
    assert fares.fuel_per_km == 12.1  # 169.9엔/L ÷ 14km/L


def test_incomplete_entry_stops_instead_of_loading_zero() -> None:
    with pytest.raises(KeyError):
        fares_from({"transit": ENTRY["transit"], "taxi": ENTRY["taxi"]})


def test_tokyo_fares_match_the_checked_figures() -> None:
    """city_fares.toml의 도쿄 값: 도쿄메트로 승차권 운임과 2026-04-20 택시 운임(출처는 그 파일)."""
    tokyo = EngineCity.model_validate(read_city_fares(CITY_FARES)["tokyo"].model_dump())
    assert [transit_fare(km, tokyo) for km in (1, 6, 6.1, 11, 19, 27, 40, 55)] == [
        180,
        180,
        210,
        210,
        260,
        300,
        330,
        330,
    ]
    assert tokyo.taxi is not None
    assert taxi_fare(1.0, tokyo.taxi) == 500
    assert taxi_fare(1.232, tokyo.taxi) == 600  # 1km 뒤 232m → 100엔 추가


def test_fare_table_rejects_unknown_keys() -> None:
    """PRD가 예전에 쓰던 car.fuel_per_km 같은 키는 조용히 무시되지 않고 오류가 난다."""
    with pytest.raises(ValueError, match="car"):
        Fares.model_validate({"fareBands": [], "car": {"fuel_per_km": 12}})


@needs_db
def test_load_fares_writes_what_the_engine_reads(conn: Connection, tmp_path: Path) -> None:
    missing = load_fares(conn, {"tokyo": fares_from(ENTRY), "atlantis": fares_from(ENTRY)})
    assert missing == ["atlantis"]
    stored = conn.execute(select(City.fare_table).where(City.code == "tokyo")).scalar_one()
    assert Fares.model_validate(stored) == fares_from(ENTRY)
    assert set(stored) == {"fareBands", "taxi", "fuelPerKm"}
