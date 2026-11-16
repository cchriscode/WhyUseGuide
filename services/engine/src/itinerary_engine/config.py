import json
import math
from functools import cache
from importlib import resources
from typing import Any


@cache
def constants() -> dict[str, Any]:
    """PRD §5.0 상수 (packages/engine-config/constants.json의 사본)."""
    text = resources.files("itinerary_engine").joinpath("constants.json").read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def round2(x: float) -> float:
    """소수 둘째 자리 반올림(0.5 올림). Python round()는 짝수 반올림이라 TS(Math.round)와 결과가 달라진다."""
    return math.floor(x * 100 + 0.5) / 100


def ceil_min(x: float) -> int:
    """분 올림. 부동소수점 오차로 정수 바로 위가 올림되지 않게 작은 여유를 둔다."""
    return math.ceil(x - 1e-9)


def ceil_to(x: int, step: int) -> int:
    return -(-x // step) * step
