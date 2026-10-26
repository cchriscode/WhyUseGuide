"""공통 픽스처(/engine-fixtures) 실행. Python·TS 엔진이 같은 입력에 같은 결과를 내는지 확인하는 데 쓴다."""

from typing import Any

from itinerary_engine.adjust import auto_adjust
from itinerary_engine.compute import compute
from itinerary_engine.models import Day, Place
from itinerary_engine.suggest import suggest


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
