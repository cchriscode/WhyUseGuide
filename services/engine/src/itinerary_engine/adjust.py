"""자동 조정 autoAdjust(day, cur) (PRD §5.4).

cur = 지금 있는 장소의 인덱스. 이미 다녀온 곳과 지금 있는 곳(0..cur)은 건드리지 않는다. 출발 전이면 -1.
"""

import itertools
import math
from collections.abc import Iterable

from itinerary_engine.compute import compute
from itinerary_engine.config import constants
from itinerary_engine.models import Day, DayResult, EngineModel, Item, StopRow


class DropSuggestion(EngineModel):
    place_id: str
    name: str


class FillGap(EngineModel):
    position: int  # 이 장소 앞 구간에 들를 곳을 넣는다(추천 시트 "사이 들르기")
    wait_min: int


class AdjustResult(EngineModel):
    items: tuple[Item, ...]
    result: DayResult
    applied: bool
    messages: tuple[str, ...]
    drop_suggestion: DropSuggestion | None = None
    fill_gap: FillGap | None = None
    undo_items: tuple[Item, ...]  # 되돌리기: 조정 전 순서·체류


def particle(word: str, with_final: str, without_final: str) -> str:
    """한국어 조사: 받침이 있으면 with_final(을·이), 없으면 without_final(를·가).

    한글로 끝나지 않으면 "을(를)"처럼 둘 다 적는다.
    """
    last = word[-1] if word else ""
    if "가" <= last <= "힣":
        return with_final if (ord(last) - 0xAC00) % 28 else without_final
    return f"{with_final}({without_final})"


def _stops(result: DayResult) -> list[StopRow]:
    return [r for r in result.rows if isinstance(r, StopRow)]


def _key(result: DayResult) -> tuple[int, bool, int]:
    """좋은 순서: 문제 수 → 종료 초과 → 이동 시간."""
    t = result.totals
    return (t.issues, t.over_end, t.move_min)


def _ok(result: DayResult) -> bool:
    return result.totals.issues == 0 and not result.totals.over_end


def _with_items(day: Day, items: Iterable[Item]) -> Day:
    return day.model_copy(update={"items": tuple(items)})


def _stay(day: Day, item: Item) -> int:
    return item.stay_min if item.stay_min is not None else day.places[item.place_id].default_stay_min


def _reorder(day: Day, cur: int) -> Day:
    head, tail = list(day.items[: cur + 1]), list(day.items[cur + 1 :])
    if len(tail) < 2:
        return day
    best_day, best = day, _key(compute(day))
    if len(tail) <= constants()["permuteMax"]:
        for order in itertools.permutations(range(len(tail))):
            cand = _with_items(day, head + [tail[i] for i in order])
            k = _key(compute(cand))
            if k < best:
                best_day, best = cand, k
        return best_day
    # 2-opt: 구간을 뒤집어 나아지면 받아들이고, 더 나아지지 않을 때까지 반복한다.
    improved = True
    while improved:
        improved = False
        current = list(best_day.items[cur + 1 :])
        for i in range(len(current) - 1):
            for j in range(i + 1, len(current)):
                swapped = current[:i] + current[i : j + 1][::-1] + current[j + 1 :]
                cand = _with_items(day, head + swapped)
                k = _key(compute(cand))
                if k < best:
                    best_day, best, improved = cand, k, True
                    break
            if improved:
                break
    return best_day


def _trim(day: Day, cur: int, original: dict[str, int]) -> Day:
    c = constants()
    floor = {
        pid: max(c["trimFloor"]["minMin"], math.ceil(stay * c["trimFloor"]["ratio"]))
        for pid, stay in original.items()
    }
    while True:
        result = compute(day)
        stops = _stops(result)
        problem = next((i for i, s in enumerate(stops) if i > cur and s.issue in ("late", "close")), None)
        if problem is not None:
            last = problem if stops[problem].issue == "close" else problem - 1
            span = range(cur + 1, last + 1)
        elif result.totals.over_end:
            span = range(cur + 1, len(stops))
        else:
            return day
        candidates = [
            i
            for i in span
            if not day.items[i].fixed_at and _stay(day, day.items[i]) > floor[day.items[i].place_id]
        ]
        if not candidates:
            return day
        target = max(candidates, key=lambda i: (_stay(day, day.items[i]), -i))  # 가장 긴 체류, 같으면 앞쪽
        item = day.items[target]
        new_stay = max(floor[item.place_id], _stay(day, item) - c["trimStepMin"])
        items = list(day.items)
        items[target] = item.model_copy(update={"stay_min": new_stay})
        day = _with_items(day, items)


def _delay_message(day: Day) -> str | None:
    if not day.delay or day.delay.minutes <= 0:
        return None
    without = compute(day.model_copy(update={"delay": None}))
    with_delay = compute(day)
    if without.totals.end == with_delay.totals.end:
        return f"{day.delay.minutes}분 지연은 예약 전 대기 시간으로 흡수돼요"
    return f"뒤 일정이 {day.delay.minutes}분씩 밀렸어요"


def auto_adjust(day: Day, cur: int = -1) -> AdjustResult:
    original_items = day.items
    original_stay = {it.place_id: _stay(day, it) for it in day.items}
    messages: list[str] = []
    delay_msg = _delay_message(day)
    if delay_msg:
        messages.append(delay_msg)

    before = compute(day)
    if _ok(before):
        return AdjustResult(
            items=day.items, result=before, applied=False, messages=tuple(messages), undo_items=day.items
        )

    adjusted = _trim(_reorder(day, cur), cur, original_stay)
    result = compute(adjusted)

    old_pos = {it.place_id: i for i, it in enumerate(original_items)}
    moved = [
        (day.places[it.place_id].name, i + 1)
        for i, it in enumerate(adjusted.items)
        if old_pos[it.place_id] != i
    ]
    if moved:
        parts = [f"{name}{particle(name, '을', '를')} {pos}번째로" for name, pos in moved]
        messages.append("순서를 바꿨어요: " + ", ".join(parts))
    for it in adjusted.items:
        stay = _stay(adjusted, it)
        if stay != original_stay[it.place_id]:
            messages.append(f"{day.places[it.place_id].name} 체류 {original_stay[it.place_id]}분 → {stay}분")

    drop: DropSuggestion | None = None
    fill: FillGap | None = None
    stops = _stops(result)
    remaining = [s for s in stops if s.issue]
    if remaining and all(s.issue == "open" for s in remaining) and not result.totals.over_end:
        first = next(i for i, s in enumerate(stops) if s.issue == "open")
        fill = FillGap(position=first, wait_min=stops[first].wait)
        messages.append(f"{stops[first].name} 오픈까지 {stops[first].wait}분이 비어요")
    elif remaining or result.totals.over_end:
        candidates = [
            (day.places[it.place_id].rating, i, it.place_id)
            for i, it in enumerate(adjusted.items)
            if i > cur and not it.fixed_at
        ]
        if candidates:
            _, _, pid = min(candidates)
            drop = DropSuggestion(place_id=pid, name=day.places[pid].name)

    end = result.totals.end
    if _ok(result):
        messages.append(f"조정 후 {end} 종료 · 예약과 영업시간 모두 맞아요")
    elif fill is None:
        problems = result.totals.issues + (1 if result.totals.over_end else 0)
        over = " · 종료 목표를 넘어요" if result.totals.over_end else ""
        tail = f" · {drop.name} 빼기를 제안해요" if drop else ""
        messages.append(f"조정 후 {end} 종료 · 아직 맞지 않는 곳 {problems}건{over}{tail}")

    return AdjustResult(
        items=adjusted.items,
        result=result,
        applied=adjusted.items != original_items,
        messages=tuple(messages),
        drop_suggestion=drop,
        fill_gap=fill,
        undo_items=original_items,
    )
