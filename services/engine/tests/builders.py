"""테스트용 입력. 도우미는 itinerary_engine.fixtures의 것을 쓴다(픽스처 생성 스크립트와 같다)."""

from typing import Any

from itinerary_engine.fixtures import all_legs, day_input, leg, place
from itinerary_engine.models import Day, DayResult, StopRow

__all__ = ["all_legs", "day", "leg", "place", "stops"]


def day(
    items: list[dict[str, Any]], places: list[dict[str, Any]], legs: list[dict[str, Any]], **kw: Any
) -> Day:
    return Day.model_validate(day_input(items, places, legs, **kw))


def stops(result: DayResult) -> list[StopRow]:
    return [r for r in result.rows if isinstance(r, StopRow)]
