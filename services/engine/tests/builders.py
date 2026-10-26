"""테스트용 입력 생성. 구간 시간은 known leg로 직접 정해 계산을 손으로 따라갈 수 있게 한다."""

from typing import Any

from itinerary_engine.models import Day, DayResult, StopRow


def place(pid: str, **kw: Any) -> dict[str, Any]:
    return {"id": pid, "name": pid, "type": "museum", "lat": 35.68, "lon": 139.70, "defaultStayMin": 60, **kw}


def leg(
    a: str, b: str, minutes: int, fare: int = 180, mode: str = "transit", plan: str = "balance"
) -> dict[str, Any]:
    return {"from": a, "to": b, "planMode": plan, "mode": mode, "minutes": minutes, "fare": fare, "km": 3.0}


def all_legs(ids: list[str], minutes: int) -> list[dict[str, Any]]:
    """모든 장소 쌍(양방향)에 같은 이동 시간을 준다. 순서를 바꿔 봐도 이동 시간이 같게."""
    return [leg(a, b, minutes) for a in ids for b in ids if a != b]


def day(
    items: list[dict[str, Any]], places: list[dict[str, Any]], legs: list[dict[str, Any]], **kw: Any
) -> Day:
    return Day.model_validate(
        {
            "date": "2026-10-27",
            "start": "09:00",
            "items": items,
            "places": {p["id"]: p for p in places},
            "legs": legs,
            **kw,
        }
    )


def stops(result: DayResult) -> list[StopRow]:
    return [r for r in result.rows if isinstance(r, StopRow)]
