"""공통 픽스처(/engine-fixtures) 지원: 위치, 입력 도우미, 실행.

Python·TS 엔진이 같은 입력에 같은 결과를 내는지 확인하는 데 쓴다. 픽스처를 만드는 스크립트
(scripts/make_fixtures.py)와 테스트가 같은 도우미를 쓴다.
"""

from pathlib import Path
from typing import Any

from itinerary_engine.adjust import auto_adjust
from itinerary_engine.compute import compute
from itinerary_engine.models import Day, Place
from itinerary_engine.suggest import suggest

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURES_DIR = REPO_ROOT / "engine-fixtures"  # 두 엔진의 공통 테스트 입력·기대 결과
SHARED_CONSTANTS = REPO_ROOT / "packages" / "engine-config" / "constants.json"  # constants.json 원본


def place(pid: str, **kw: Any) -> dict[str, Any]:
    return {"id": pid, "name": pid, "type": "박물관", "lat": 35.68, "lon": 139.70, "defaultStayMin": 60, **kw}


def leg(
    a: str, b: str, minutes: int, fare: int = 180, mode: str = "transit", plan: str = "balance"
) -> dict[str, Any]:
    """알려진 구간. 구간 시간을 직접 정해 계산을 손으로 따라갈 수 있게 한다."""
    return {"from": a, "to": b, "planMode": plan, "mode": mode, "minutes": minutes, "fare": fare, "km": 3.0}


def all_legs(ids: list[str], minutes: int) -> list[dict[str, Any]]:
    """모든 장소 쌍(양방향)에 같은 이동 시간을 준다. 순서를 바꿔 봐도 이동 시간이 같게."""
    return [leg(a, b, minutes) for a in ids for b in ids if a != b]


def day_input(
    items: list[dict[str, Any]], places: list[dict[str, Any]], legs: list[dict[str, Any]], **kw: Any
) -> dict[str, Any]:
    """하루 입력(JSON 모양)."""
    return {
        "date": "2026-10-27",
        "start": "09:00",
        "items": items,
        "places": {p["id"]: p for p in places},
        "legs": legs,
        **kw,
    }


def run(kind: str, payload: dict[str, Any]) -> Any:
    """kind = compute | autoAdjust | suggest. 결과는 JSON(camelCase)으로."""
    d = Day.model_validate(payload["day"])
    if kind == "compute":
        return compute(d).model_dump(by_alias=True, mode="json")
    if kind == "autoAdjust":
        return auto_adjust(d, payload["cur"]).model_dump(by_alias=True, mode="json")
    candidates = [Place.model_validate(c) for c in payload["candidates"]]
    options = suggest(d, payload["mode"], candidates, position=payload.get("position"), cur=payload["cur"])
    return [o.model_dump(by_alias=True, mode="json") for o in options]
