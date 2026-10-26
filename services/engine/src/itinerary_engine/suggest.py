"""후보 점수: 대체 · 사이 들르기 · 남는 시간 (PRD §5.5).

score = Δmove + 60·N_new + 80·I_hours + 45·I_late_end − 6·r − 25·I_same_type − 20·I_absorbed (낮을수록 좋다)
"""

import math
from typing import Literal

from itinerary_engine.compute import compute
from itinerary_engine.config import constants, to_min
from itinerary_engine.models import Day, DayResult, EngineModel, Item, Place, StopRow

SuggestMode = Literal["replace", "between", "fill"]


class SuggestOption(EngineModel):
    place_id: str
    name: str
    position: int  # 이 인덱스에 넣는다(대체 모드는 바꿀 장소의 자리)
    score: float
    detour_min: int  # 넣은 뒤 이동 시간 − 넣기 전 이동 시간
    arrive: str
    end_after: str
    absorbed: bool
    same_type: bool
    outside_hours: bool
    new_issues: int
    badges: tuple[str, ...]
    effect: str
    warning: str | None = None


def _round1(x: float) -> float:
    return math.floor(x * 10 + 0.5) / 10


def _stop_at(result: DayResult, index: int) -> StopRow:
    return [r for r in result.rows if isinstance(r, StopRow)][index]


def _issues_except(result: DayResult, index: int) -> int:
    return sum(
        1 for i, r in enumerate(r for r in result.rows if isinstance(r, StopRow)) if r.issue and i != index
    )


def _effect(detour: int, end_delta: int) -> str:
    move = (
        f"{detour}분 돌아가요"
        if detour > 0
        else ("이동 그대로" if detour == 0 else f"이동이 {-detour}분 줄어요")
    )
    end = "끝나는 시간 그대로" if end_delta <= 0 else f"끝나는 시간 {end_delta}분 늦어져요"
    return f"{move} · {end}"


def _evaluate(
    day: Day,
    base: DayResult,
    candidate: Place,
    position: int,
    removed_type: str | None,
) -> SuggestOption:
    c = constants()
    w = c["suggestScore"]
    items = list(day.items)
    items.insert(position, Item(place_id=candidate.id))
    trial = day.model_copy(update={"items": tuple(items), "places": {**day.places, candidate.id: candidate}})
    after = compute(trial)
    row = _stop_at(after, position)

    detour = after.totals.move_min - base.totals.move_min
    end_delta = to_min(after.totals.end) - to_min(base.totals.end)
    new_issues = max(0, _issues_except(after, position) - base.totals.issues)
    outside = row.issue is not None
    late_end = after.totals.over_end
    same = removed_type is not None and candidate.type == removed_type
    absorbed = end_delta <= 0

    score = (
        w["move"] * detour
        + w["newIssue"] * new_issues
        + w["outsideHours"] * outside
        + w["lateEnd"] * late_end
        + w["rating"] * candidate.rating
        + w["sameType"] * same
        + w["absorbed"] * absorbed
    )
    badges = [
        label
        for label, on in (
            ("기다리는 시간 활용", absorbed),
            ("비슷한 곳", same),
            ("가는 길", detour <= c["badges"]["onTheWayMaxDetourMin"]),
            ("실내", candidate.indoor),
        )
        if on
    ]
    warning = None
    if outside:
        warning = "영업시간 밖이에요"
    elif new_issues:
        warning = f"다른 일정 {new_issues}곳이 맞지 않게 돼요"
    return SuggestOption(
        place_id=candidate.id,
        name=candidate.name,
        position=position,
        score=_round1(score),
        detour_min=detour,
        arrive=row.arrive,
        end_after=after.totals.end,
        absorbed=absorbed,
        same_type=same,
        outside_hours=outside,
        new_issues=new_issues,
        badges=tuple(badges),
        effect=_effect(detour, end_delta),
        warning=warning,
    )


def suggest(
    day: Day,
    mode: SuggestMode,
    candidates: list[Place],
    position: int | None = None,
    cur: int = -1,
    limit: int = 3,
) -> list[SuggestOption]:
    """mode
    - replace: position의 장소를 후보로 바꾼다. 기준은 지금 일정(바꾸기 전).
    - between: position(이 인덱스 앞 구간)에 후보를 넣는다.
    - fill: 지금 있는 곳(cur) 뒤 모든 구간에 넣어 보고 가장 좋은 자리를 쓴다.
    """
    in_day = {it.place_id for it in day.items}
    pool = [p for p in candidates if p.id not in in_day]
    base = compute(day)
    options: list[SuggestOption] = []
    if mode == "replace":
        if position is None:
            raise ValueError("replace needs position")
        removed = day.places[day.items[position].place_id]
        without = day.model_copy(update={"items": day.items[:position] + day.items[position + 1 :]})
        options = [_evaluate(without, base, p, position, removed.type) for p in pool]
    elif mode == "between":
        if position is None:
            raise ValueError("between needs position")
        options = [_evaluate(day, base, p, position, None) for p in pool]
    else:
        positions = range(cur + 1, len(day.items) + 1)
        for p in pool:
            tried = [_evaluate(day, base, p, pos, None) for pos in positions]
            if tried:
                options.append(min(tried, key=lambda o: (o.score, o.position)))
    options.sort(key=lambda o: (o.score, o.place_id))
    return options[:limit]
