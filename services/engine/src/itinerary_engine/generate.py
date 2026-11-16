"""일정 생성 generate(trip) (PRD §5.2). 서버 전용(앱 엔진에는 없다).

1 후보 점수 → 2 하루 장소 수 → 3 날짜별 묶기(k-평균) → 4 식사 배치 → 5 순서(OR-Tools 시간창 외판원)
→ 6 이동 상한 맞추기(가까운 곳 교체) → 7 이동 방식별 3안.
같은 입력이면 같은 결과가 나오도록 메타휴리스틱(시간 제한 탐색)은 쓰지 않는다.
"""

import math
from collections.abc import Callable
from typing import Literal

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from itinerary_engine.compute import compute
from itinerary_engine.config import constants, to_min
from itinerary_engine.models import (
    START,
    City,
    Day,
    DayResult,
    EngineModel,
    Item,
    Leg,
    Pace,
    Place,
    PlanMode,
    Point,
    Transport,
)
from itinerary_engine.route import Endpoint, choose_leg

AVOID_TYPE_PENALTY = 0.1  # 별점 2 이하였던 날의 유형(§5.6). 점수 범위 0~1 기준
CROWD_POPULARITY = 4  # 이 유명도 이상은 붐빈다고 본다(§5.6 "사람이 너무 많았어요")
CROWD_MORNING_END = "12:00"
LUNCH_TYPES = {"맛집", "시장"}
DINNER_TYPES = {"맛집", "바 골목", "시장"}
PLAN_MODES: tuple[PlanMode, ...] = ("fast", "balance", "cheap")


class Profile(EngineModel):
    """추천용 장소 정보(날짜와 무관)."""

    tags: dict[str, float] = {}
    popularity: int = 3  # 1(로컬) ~ 5(유명)
    meal: Literal["lunch", "dinner", "both"] | None = None


class FixedBooking(EngineModel):
    place_id: str
    date: str
    time: str


class Prefs(EngineModel):
    """코스 평가로 바뀐 가중치(§5.6)."""

    place_count_delta: int = 0
    slack_min: int = 0
    move_cap_factor: float = 1.0
    mix_shift: int = 0
    tag_weights: dict[str, float] = {}
    crowd_avoid: bool = False  # 유명한 곳(유명도 4~5)은 오전에
    booking_first: bool = False  # 예약 가능 장소는 예약 버튼 먼저(앱 표시용)
    avoid_types: tuple[str, ...] = ()  # 별점 2 이하였던 날의 유형: 다음 날 비중을 낮춘다


class GenerateInput(EngineModel):
    dates: tuple[str, ...]
    tastes: tuple[str, ...]
    places: dict[str, dict[str, Place]]  # 날짜 → 그날 정보로 정리된 장소(없거나 closed면 그날 못 감)
    profiles: dict[str, Profile]
    start: str = "09:00"
    pace: Pace = "normal"
    mix: int = 50  # 0 유명 ~ 100 로컬
    fixed: tuple[FixedBooking, ...] = ()
    city: City = City()
    city_move_cap_factor: float = 1.0
    transport: Transport = "transit"
    legs: tuple[Leg, ...] = ()
    start_point: Point | None = None
    end_point: Point | None = None
    day_end: str | None = None
    prefs: Prefs = Prefs()


class GeneratedDay(EngineModel):
    date: str
    items: tuple[Item, ...]
    plans: dict[PlanMode, DayResult]
    move_min: int  # 균형 모드 이동 합
    move_cap_min: int
    move_over: bool  # 교체해도 상한을 넘으면 "이동이 긴 날이에요"
    swapped: tuple[tuple[str, str], ...]  # (뺀 장소, 넣은 장소)


class GenerateResult(EngineModel):
    days: tuple[GeneratedDay, ...]


# --- 1. 후보 점수


def taste_match(tastes: tuple[str, ...], tag_weights: dict[str, float], tags: dict[str, float]) -> float:
    user = {t: 1.0 for t in tastes}
    for tag, delta in tag_weights.items():
        user[tag] = max(0.0, user.get(tag, 0.0) + delta)
    dot = sum(w * tags.get(t, 0.0) for t, w in user.items())
    norm = math.sqrt(sum(w * w for w in user.values())) * math.sqrt(sum(w * w for w in tags.values()))
    return dot / norm if norm else 0.0


def candidate_score(inp: GenerateInput, place: Place, profile: Profile) -> float:
    w = constants()["candidateScore"]
    mix = min(100, max(0, inp.mix + inp.prefs.mix_shift))
    target_popularity = 5 - mix / 25  # 0 → 5(유명), 100 → 1(로컬)
    fit = 1 - abs(target_popularity - profile.popularity) / 4
    score = (
        w["taste"] * taste_match(inp.tastes, inp.prefs.tag_weights, profile.tags)
        + w["rating"] * place.rating / 5
        + w["popularity"] * fit
    )
    if place.type in inp.prefs.avoid_types:
        score -= AVOID_TYPE_PENALTY
    return float(score)


# --- 3. 날짜별 묶기


def _xy(p: Place, mid_lat: float) -> tuple[float, float]:
    """위·경도 → 거리 비율이 맞는 평면 좌표. 경도 간격은 위도에 따라 줄어든다(mid_lat = 후보 평균 위도)."""
    return (p.lon * math.cos(math.radians(mid_lat)), p.lat)


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def kmeans(
    points: dict[str, tuple[float, float]], order: list[str], k: int, rounds: int = 20
) -> dict[str, int]:
    """결정적 k-평균: 첫 중심은 order 맨 앞, 다음 중심은 기존 중심에서 가장 먼 점."""
    ids = list(order)
    k = max(1, min(k, len(ids)))
    centers = [points[ids[0]]]
    while len(centers) < k:
        far = max(ids, key=lambda i: (min(_dist(points[i], c) for c in centers), -ids.index(i)))
        centers.append(points[far])
    assign: dict[str, int] = {}
    for _ in range(rounds):
        assign = {i: min(range(k), key=lambda c: (_dist(points[i], centers[c]), c)) for i in ids}
        for c in range(k):
            members = [points[i] for i in ids if assign[i] == c]
            if members:
                centers[c] = (
                    sum(m[0] for m in members) / len(members),
                    sum(m[1] for m in members) / len(members),
                )
    return assign


# --- 5. 순서


def _endpoint(place: Place) -> Endpoint:
    return Endpoint(place.id, place.lat, place.lon, place.stations)


def solve_order(
    inp: GenerateInput,
    date: str,
    ids: list[str],
    plan_mode: PlanMode,
    stays: dict[str, int],
    fixed_at: dict[str, str],
    meals_of: dict[str, str],
) -> list[str] | None:
    """시간창 외판원(열린 경로). 해가 없으면 None."""
    c = constants()
    places = inp.places[date]
    known = {(lg.from_id, lg.to_id, lg.plan_mode): lg for lg in inp.legs}
    start_ep = Endpoint(START, inp.start_point.lat, inp.start_point.lon) if inp.start_point else None
    nodes = [_endpoint(places[i]) for i in ids]
    n = len(nodes)
    depot = n  # 출발점(숙소 또는 가상 출발점)
    start = to_min(inp.start)
    horizon = to_min(inp.day_end or c["dayEnd"]) + 120

    def travel(a: int, b: int) -> int:
        if b == depot:
            return 0  # 열린 경로: 마지막 장소에서 끝난다
        if a == depot:
            if start_ep is None:
                return 0
            return choose_leg(start_ep, nodes[b], plan_mode, inp.transport, inp.city, known).minutes
        return choose_leg(nodes[a], nodes[b], plan_mode, inp.transport, inp.city, known).minutes

    manager = pywrapcp.RoutingIndexManager(n + 1, 1, depot)
    routing = pywrapcp.RoutingModel(manager)

    def transit_cb(fi: int, ti: int) -> int:
        a, b = manager.IndexToNode(fi), manager.IndexToNode(ti)
        service = stays.get(ids[a], places[ids[a]].default_stay_min) if a != depot else 0
        return service + travel(a, b)

    cb = routing.RegisterTransitCallback(transit_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    routing.AddDimension(cb, 24 * 60, horizon, False, "time")
    time = routing.GetDimensionOrDie("time")
    time.CumulVar(routing.Start(0)).SetRange(start, start)
    # 이동뿐 아니라 하루 전체 길이(대기 포함)에도 비용을 매겨,
    # 식사·오픈 시간창 앞에서 오래 기다리는 순서를 피한다.
    time.SetSpanCostCoefficientForAllVehicles(1)

    meals = c["meals"]
    for i, pid in enumerate(ids):
        p = places[pid]
        lo, hi = start, horizon - stays[pid]
        if p.open:
            lo = max(lo, to_min(p.open))
        if p.close:
            hi = min(hi, to_min(p.close) - stays[pid])
        if p.last_entry:
            hi = min(hi, to_min(p.last_entry))
        if pid in fixed_at:
            t = to_min(fixed_at[pid])
            lo, hi = max(lo, t), min(hi, t + c["fixedLateTolMin"])
        meal = meals_of.get(pid)
        if meal:
            center, spread = meals[meal]
            lo, hi = max(lo, to_min(center) - spread), min(hi, to_min(center) + spread)
        if lo > hi:
            return None
        index = manager.NodeToIndex(i)
        time.CumulVar(index).SetRange(lo, hi)
        if inp.prefs.crowd_avoid and inp.profiles.get(pid, Profile()).popularity >= CROWD_POPULARITY:
            # 오전 우선: 넘기면 분당 비용(강제는 아님)
            time.SetCumulVarSoftUpperBound(index, to_min(CROWD_MORNING_END), 10)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    solution = routing.SolveWithParameters(params)
    if solution is None:
        return None
    order = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node != depot:
            order.append(ids[node])
        index = solution.Value(routing.NextVar(index))
    return order


# --- 전체


def generate(inp: GenerateInput) -> GenerateResult:
    c = constants()
    per_day = max(1, c["placesPerDay"][inp.pace] + inp.prefs.place_count_delta)
    fixed_by_date: dict[str, list[FixedBooking]] = {}
    for fb in inp.fixed:
        fixed_by_date.setdefault(fb.date, []).append(fb)

    # 1. 후보 점수 (어느 날이든 갈 수 있는 장소)
    available = {pid: p for date in inp.dates for pid, p in inp.places.get(date, {}).items() if not p.closed}
    scores = {pid: candidate_score(inp, p, inp.profiles.get(pid, Profile())) for pid, p in available.items()}
    ranked = sorted(scores, key=lambda pid: (-scores[pid], pid))
    fixed_ids = {fb.place_id for fb in inp.fixed}
    top = ranked[: len(inp.dates) * per_day * 2]
    top += [pid for pid in sorted(fixed_ids) if pid not in top]

    # 3. 날짜별 묶기
    mid_lat = sum(available[pid].lat for pid in top) / len(top)
    clusters = kmeans({pid: _xy(available[pid], mid_lat) for pid in top}, top, len(inp.dates))
    date_cluster: dict[str, int] = {}
    used: set[int] = set()
    for date in inp.dates:
        for fb in fixed_by_date.get(date, []):
            cl = clusters.get(fb.place_id)
            if cl is not None and cl not in used:
                date_cluster[date] = cl
                used.add(cl)
                break
    rest = sorted(
        {cl for cl in clusters.values()} - used,
        key=lambda cl: (-sum(scores[p] for p, x in clusters.items() if x == cl), cl),
    )
    for date in inp.dates:
        if date not in date_cluster:
            date_cluster[date] = rest.pop(0) if rest else 0

    taken: set[str] = set(fixed_ids)
    days = [
        _one_day(inp, date, per_day, scores, clusters, date_cluster[date], fixed_by_date, taken)
        for date in inp.dates
    ]
    return GenerateResult(days=tuple(days))


def _meal_candidates(inp: GenerateInput, pool: list[str], date: str, meal: str) -> list[str]:
    types = LUNCH_TYPES if meal == "lunch" else DINNER_TYPES
    out = []
    for pid in pool:
        prof = inp.profiles.get(pid, Profile())
        if prof.meal in (meal, "both") or (prof.meal is None and inp.places[date][pid].type in types):
            out.append(pid)
    return out


def _one_day(
    inp: GenerateInput,
    date: str,
    per_day: int,
    scores: dict[str, float],
    clusters: dict[str, int],
    cluster: int,
    fixed_by_date: dict[str, list[FixedBooking]],
    taken: set[str],
) -> GeneratedDay:
    c = constants()
    places = inp.places.get(date, {})
    fixed_at = {fb.place_id: fb.time for fb in fixed_by_date.get(date, []) if fb.place_id in places}

    def open_today(pid: str) -> bool:
        return pid in places and not places[pid].closed

    pool = sorted(
        (pid for pid, cl in clusters.items() if cl == cluster and open_today(pid) and pid not in taken),
        key=lambda pid: (-scores[pid], pid),
    )
    chosen = list(fixed_at)
    meal_map: dict[str, str] = {}  # 장소 → lunch/dinner (식사 시간창)
    # 4. 식사: 점심·저녁 한 곳씩(가능하면)
    for meal in ("lunch", "dinner"):
        if len(chosen) >= per_day:
            break
        for pid in _meal_candidates(inp, pool, date, meal):
            if pid not in chosen:
                chosen.append(pid)
                meal_map[pid] = meal
                break
    for pid in pool:
        if len(chosen) >= per_day:
            break
        if pid not in chosen:
            chosen.append(pid)

    stays = {pid: places[pid].default_stay_min + inp.prefs.slack_min for pid in chosen}

    def stay_of(pid: str) -> int:
        return stays.get(pid, places[pid].default_stay_min + inp.prefs.slack_min)

    def order_for(ids: list[str], mode: PlanMode) -> list[str]:
        """해가 없으면 평점 낮은(고정 예약이 아닌) 장소부터 빼고 다시 푼다."""
        ids = list(ids)
        while ids:
            order = solve_order(inp, date, ids, mode, stays, fixed_at, meal_map)
            if order is not None:
                return order
            droppable = [pid for pid in ids if pid not in fixed_at]
            if not droppable:
                break
            ids.remove(min(droppable, key=lambda pid: (places[pid].rating, pid)))
        return ids

    def day_for(order: list[str], mode: PlanMode) -> Day:
        return Day(
            date=date,
            start=inp.start,
            items=tuple(
                Item(place_id=pid, stay_min=stay_of(pid), fixed_at=fixed_at.get(pid)) for pid in order
            ),
            places=places,
            city=inp.city,
            plan_mode=mode,
            transport=inp.transport,
            legs=inp.legs,
            start_point=inp.start_point,
            end_point=inp.end_point,
            day_end=inp.day_end,
        )

    order = order_for(chosen, "balance")

    # 6. 이동 상한 맞추기 (§5.2-6, §13.4)
    if inp.transport == "car":
        cap = round(c["driveCapMin"][inp.pace] * inp.prefs.move_cap_factor)
    else:
        cap = round(c["moveCapMin"][inp.pace] * inp.city_move_cap_factor * inp.prefs.move_cap_factor)
    swapped: list[tuple[str, str]] = []
    result = compute(day_for(order, "balance"))
    for _ in range(c["moveCapSwapMax"]):
        if result.totals.move_min <= cap:
            break
        swap = _best_swap(order, result, pool, scores, fixed_at, meal_map, inp, date, day_for)
        if swap is None:
            break
        out_id, in_id = swap
        stays[in_id] = places[in_id].default_stay_min + inp.prefs.slack_min
        if out_id in meal_map:
            meal_map[in_id] = meal_map.pop(out_id)
        order = order_for([in_id if pid == out_id else pid for pid in order], "balance")
        swapped.append(swap)
        result = compute(day_for(order, "balance"))

    taken.update(order)
    # 7. 이동 방식별 3안 (자동차 여행은 1안)
    modes: tuple[PlanMode, ...] = ("balance",) if inp.transport == "car" else PLAN_MODES
    plans = {mode: compute(day_for(order_for(order, mode), mode)) for mode in modes}
    return GeneratedDay(
        date=date,
        items=day_for(order, "balance").items,
        plans=plans,
        move_min=result.totals.move_min,
        move_cap_min=cap,
        move_over=result.totals.move_min > cap,
        swapped=tuple(swapped),
    )


def _best_swap(
    order: list[str],
    result: DayResult,
    pool: list[str],
    scores: dict[str, float],
    fixed_at: dict[str, str],
    meal_map: dict[str, str],
    inp: GenerateInput,
    date: str,
    day_for: Callable[[list[str], PlanMode], Day],
) -> tuple[str, str] | None:
    """뺐을 때 이동이 가장 많이 줄어드는 장소를, 점수가 충분한 후보 중 이동이 가장 짧아지는 곳으로 바꾼다."""
    c = constants()
    base = result.totals.move_min
    removable = [pid for pid in order if pid not in fixed_at]
    if not removable:
        return None

    def move_of(ids: list[str]) -> int:
        return compute(day_for(ids, "balance")).totals.move_min

    out_id = max(removable, key=lambda pid: (base - move_of([p for p in order if p != pid]), pid))
    meal = meal_map.get(out_id)
    candidates = [
        pid
        for pid in pool
        if pid not in order
        and scores[pid] >= c["moveCapScoreMin"] * scores[out_id]
        and (meal is None or pid in _meal_candidates(inp, [pid], date, meal))
    ]
    if not candidates:
        return None
    in_id = min(candidates, key=lambda pid: (move_of([pid if p == out_id else p for p in order]), pid))
    if move_of([in_id if p == out_id else p for p in order]) >= base:
        return None
    return (out_id, in_id)
