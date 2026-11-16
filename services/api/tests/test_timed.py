"""시간표 구간(열차에 맞춘 출발 시각). OTP는 가짜 HTTP 응답·가짜 시간표로 대신한다(DB 불필요)."""

import json
import logging
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

import anyio
import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from itinerary_engine.compute import compute
from itinerary_engine.config import to_hhmm, to_min
from itinerary_engine.models import City, Day, Leg, LegRow, PlanMode, StopRow
from itinerary_engine.route import Endpoint
from whyuseguide_api.timed import (
    CityTransit,
    OtpPlanner,
    RedisLegCache,
    cache_key,
    ready_ms,
    timed_compute,
    to_leg,
)

DATE = "2026-10-27"
A = Endpoint("A", 35.7148, 139.7967)  # 센소지
B = Endpoint("B", 35.7188, 139.7765)  # 도쿄 국립박물관(경로 약 2.4km → 균형은 대중교통, 알뜰은 도보)
FAR = Endpoint("F", 35.6812, 139.7671)  # 도쿄역(경로 약 6km)
NEAR = Endpoint("N", 35.7119, 139.7963)  # 나카미세(약 300m → 도보)
TOEI, JR = "東京都交通局", "東日本旅客鉄道"
TOKYO = CityTransit(tz=ZoneInfo("Asia/Tokyo"), estimated_agencies=frozenset({JR}))


def itinerary(
    start: float,
    end: float,
    agencies: list[str],
    transfers: int = 0,
    fares: list[tuple[str, int]] | None = None,
) -> dict[str, Any]:
    """ready(10:00) 기준 분으로 만든 OTP 경로. fares = 승차 구간별 (요금 상품 id, 엔). 없으면 운임표 없음."""
    base = ready_ms(DATE, 600, TOKYO.tz)
    legs: list[dict[str, Any]] = [{"mode": "WALK", "distance": 400.0, "route": None}]
    for i, a in enumerate(agencies):
        leg: dict[str, Any] = {"mode": "SUBWAY", "distance": 2000.0, "route": {"agency": {"name": a}}}
        if fares and fares[i]:
            leg["fareProducts"] = [{"id": fares[i][0], "product": {"price": {"amount": float(fares[i][1])}}}]
        legs.append(leg)
    return {
        "startTime": base + int(start * 60000),
        "endTime": base + int(end * 60000),
        "numberOfTransfers": transfers,
        "legs": legs,
    }


def otp_planner(handle: Callable[[httpx.Request], httpx.Response]) -> OtpPlanner:
    """가짜 HTTP 응답으로 도는 OtpPlanner."""
    return OtpPlanner(httpx.AsyncClient(transport=httpx.MockTransport(handle)), "http://otp", concurrency=2)


def plan_body(its: list[dict[str, Any]], *errors: str) -> dict[str, Any]:
    return {"data": {"plan": {"routingErrors": [{"code": e} for e in errors], "itineraries": its}}}


def planner(
    its: list[dict[str, Any]], *errors: str, seen: list[dict[str, Any]] | None = None, status: int = 200
) -> OtpPlanner:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content)["variables"])
        return httpx.Response(status, json=plan_body(its, *errors))

    return otp_planner(handle)


def ask(p: OtpPlanner, b: Endpoint = B, mode: PlanMode = "balance") -> Leg | None:
    async def go() -> Leg | None:
        return await p.leg(A, b, DATE, 600, mode, City(), TOKYO)

    return anyio.run(go)


def test_to_leg_waits_down_and_arrives_up() -> None:
    leg = to_leg(itinerary(7.5, 25.2, [TOEI]), A, B, "balance", 600, ready_ms(DATE, 600, TOKYO.tz), City())
    assert (leg.depart_at, leg.wait_min, leg.minutes) == ("10:00", 7, 19)  # 10:07 출발, 10:26 도착
    assert leg.mode == "transit" and not leg.estimated and leg.km == 2.4


def test_balance_takes_earliest_arrival_and_marks_estimated_lines() -> None:
    its = [itinerary(3, 30, [TOEI]), itinerary(8, 26, [JR], 0), itinerary(0, 35, [])]  # 도보만은 더 늦어 뺀다
    leg = ask(planner(its))
    assert leg is not None and (leg.wait_min, leg.minutes) == (8, 18)
    assert leg.estimated  # 근사 노선(JR)을 탄 경로는 "예상"


def test_balance_walks_when_otp_says_walking_is_better() -> None:
    """도보 기준을 넘어도 OTP가 걷는 게 낫다고 하면 근사 대신 OTP 걷기 경로."""
    leg = ask(planner([itinerary(0, 31, [])], "WALKING_BETTER_THAN_TRANSIT"))
    assert leg is not None and (leg.mode, leg.wait_min, leg.minutes, leg.estimated) == ("walk", 0, 31, False)


def test_walk_only_without_reason_falls_back() -> None:
    """정류장은 있는데 그 시각에 버스가 없을 때 등: 이유 없이 걷기 경로만 오면 근사("예상")."""
    for mode in ("balance", "fast", "cheap"):
        assert ask(planner([itinerary(0, 140, [])]), b=FAR, mode=mode) is None


def test_no_stops_in_range_falls_back_instead_of_hours_of_walking() -> None:
    """실데이터: 근처에 역이 없으면 OTP가 215분 걷기 경로를 낸다. 데이터 공백이라 근사("예상")로 둔다."""
    seen: list[dict[str, Any]] = []
    for mode in ("balance", "fast", "cheap"):
        assert ask(planner([itinerary(0, 215, [])], "NO_STOPS_IN_RANGE", seen=seen), b=FAR, mode=mode) is None
    assert len(seen) == 3  # 요청은 실제로 나갔다(요청 실패로 None이 된 게 아니다)


def test_balance_walks_when_every_transit_option_arrives_later() -> None:
    """실데이터에서 나온 경우: 걸으면 48분, 대중교통은 19분 기다려 40분 걷고 3분 타서 65분."""
    for mode in ("balance", "cheap"):
        leg = ask(planner([itinerary(0, 48, []), itinerary(19, 65, [TOEI])]), b=FAR, mode=mode)
        assert leg is not None and (leg.mode, leg.wait_min, leg.minutes) == ("walk", 0, 48)


def test_fast_may_walk_and_cheap_prefers_fewer_operators() -> None:
    its = [itinerary(2, 20, [TOEI, JR], 1), itinerary(5, 28, [TOEI]), itinerary(0, 24, [])]
    fast = ask(planner(its), mode="fast")
    assert fast is not None and (fast.mode, fast.wait_min, fast.minutes) == ("transit", 2, 18)
    cheap = ask(planner(its), b=FAR, mode="cheap")
    assert cheap is not None and (cheap.wait_min, cheap.minutes, cheap.transfers) == (5, 23, 0)


def test_short_leg_asks_walk_only_and_failure_falls_back() -> None:
    seen: list[dict[str, Any]] = []
    leg = ask(planner([itinerary(0, 6, [])], seen=seen), b=NEAR)
    assert leg is not None and (leg.mode, leg.wait_min, leg.minutes) == ("walk", 0, 6)
    assert seen[0]["modes"] == [{"mode": "WALK"}] and seen[0]["time"] == "10:00"
    assert ask(planner([])) is None


class EveryTenMinutes(OtpPlanner):
    """가짜 시간표: 열차는 매시 0·10·20…분 출발, 타고 15분. 3분 걸어 역에 간다."""

    def __init__(self, fail: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[str, str, int]] = []
        self.fail = fail

    async def leg(
        self,
        a: Endpoint,
        b: Endpoint,
        date: str,
        ready: int,
        plan_mode: PlanMode,
        city: City,
        transit: CityTransit,
    ) -> Leg | None:
        self.calls.append((a.id, b.id, ready))
        if b.id in self.fail:
            return None
        train = -(-(ready + 3) // 10) * 10
        wait = train - 3 - ready
        return Leg.model_validate(
            {
                "from": a.id,
                "to": b.id,
                "planMode": plan_mode,
                "mode": "transit",
                "minutes": 18,
                "fare": 180,
                "km": 3.0,
                "departAt": to_hhmm(ready),
                "waitMin": wait,
            }
        )


def three_stops() -> Day:
    places = {
        pid: {
            "id": pid,
            "name": pid,
            "type": "박물관",
            "lat": 35.70 + i / 100,
            "lon": 139.78,
            "defaultStayMin": stay,
        }
        for i, (pid, stay) in enumerate((("P", 62), ("Q", 47), ("R", 30)))
    }
    return Day.model_validate(
        {"date": DATE, "start": "09:00", "items": [{"placeId": p} for p in places], "places": places}
    )


def test_timed_compute_settles_on_train_times() -> None:
    p = EveryTenMinutes()
    result = anyio.run(timed_compute, three_stops(), p, TOKYO)
    legs = [r for r in result.rows if isinstance(r, LegRow)]
    stops = [r for r in result.rows if isinstance(r, StopRow)]
    # P 09:00~10:02 → 준비 10:02, 10:10 열차를 타려면 10:07 출발(5분 더 머묾) → 10:25 도착
    assert (legs[0].depart, legs[0].wait_min) == ("10:07", 5) and stops[1].arrive == "10:25"
    # Q 10:25~11:12 → 11:20 열차 → 11:17 출발 → 11:35 도착
    assert (legs[1].depart, legs[1].wait_min) == ("11:17", 5) and stops[2].arrive == "11:35"
    assert all(not leg.estimated for leg in legs)
    assert result.totals.move_min == 36  # 더 머무는 시간은 이동 시간에 넣지 않는다
    # 첫 계산(근사) 때의 준비 시각으로 한 번, 바뀐 뒤 구간만 다시 묻는다
    assert len(p.calls) <= 4 and (p.calls[-1][0], p.calls[-1][2]) == ("Q", 11 * 60 + 12)


def test_failed_leg_is_estimated_and_later_legs_still_timed() -> None:
    result = anyio.run(timed_compute, three_stops(), EveryTenMinutes(fail=frozenset({"Q"})), TOKYO)
    legs = [r for r in result.rows if isinstance(r, LegRow)]
    assert legs[0].estimated and legs[0].wait_min == 0  # P→Q는 OTP 실패 → 근사
    assert not legs[1].estimated and int(legs[1].depart[-1]) == 7  # Q→R은 열차 시각(…7분 출발)


class FixedMinutes(OtpPlanner):
    """가짜 시간표: 구간마다 정해진 분(기다림 없음). 불린 출발 준비 시각을 기록한다."""

    def __init__(self, minutes: dict[tuple[str, str], int]) -> None:
        self.minutes = minutes
        self.calls: list[tuple[str, str, int]] = []

    async def leg(
        self,
        a: Endpoint,
        b: Endpoint,
        date: str,
        ready: int,
        plan_mode: PlanMode,
        city: City,
        transit: CityTransit,
    ) -> Leg | None:
        self.calls.append((a.id, b.id, ready))
        m = self.minutes[(a.id, b.id)]
        return Leg.model_validate(
            {
                "from": a.id,
                "to": b.id,
                "planMode": plan_mode,
                "mode": "transit",
                "minutes": m,
                "fare": 180,
                "km": 2.0,
                "departAt": to_hhmm(ready),
            }
        )


def test_result_is_kept_when_ready_time_comes_back() -> None:
    """중간 단계에서 R→S 준비 시각이 12:10 → 12:20 → 12:10으로 돌아와도 처음 결과를 다시 쓴다.

    마지막 결과만 들고 있으면 이때 근사로 떨어진다(실데이터 검증에서 456구간 중 24구간).
    """
    places = {
        pid: {
            "id": pid,
            "name": pid,
            "type": "박물관",
            "lat": 35.70 + i * 0.008,
            "lon": 139.78,
            "defaultStayMin": stay,
        }
        for i, (pid, stay) in enumerate((("P", 60), ("Q", 60), ("R", 30), ("S", 30)))
    }
    day = Day.model_validate(
        {"date": DATE, "start": "09:00", "items": [{"placeId": p} for p in places], "places": places}
    )
    approx = [r.minutes for r in compute(day).rows if isinstance(r, LegRow)]
    p = FixedMinutes({("P", "Q"): approx[0] + 10, ("Q", "R"): approx[1] - 10, ("R", "S"): 7})
    result = anyio.run(timed_compute, day, p, TOKYO)
    legs = [r for r in result.rows if isinstance(r, LegRow)]
    assert [leg.estimated for leg in legs] == [False, False, False]
    rs = [c[2] for c in p.calls if c[:2] == ("R", "S")]
    assert len(rs) == 2 and rs[0] == to_min(legs[2].depart)  # 처음 물은 시각으로 돌아왔다


def test_walking_better_than_transit_asks_for_the_walk() -> None:
    answers = [
        plan_body([], "WALKING_BETTER_THAN_TRANSIT"),
        plan_body([itinerary(0, 29, [])]),
    ]
    leg = ask(otp_planner(lambda request: httpx.Response(200, json=answers.pop(0))), b=FAR)
    assert leg is not None and (leg.mode, leg.minutes) == ("walk", 29) and not answers


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(500, json={}), "HTTP 500"),
        (httpx.Response(200, json={"errors": [{"message": "Validation error"}]}), "GraphQL 오류"),
        (httpx.Response(200, text="not json"), "응답을 해석할 수 없다"),
        (httpx.Response(200, json={"data": {"plan": None}}), "응답을 해석할 수 없다"),
    ],
)
def test_otp_failure_is_logged_and_falls_back(
    response: httpx.Response, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    """OTP 실패는 근사로 넘어가되(None), 조용히 넘어가지 않는다."""
    with caplog.at_level(logging.WARNING, logger="whyuseguide_api.timed"):
        assert ask(otp_planner(lambda request: response)) is None
    assert "OTP 질의 실패" in caplog.text and reason in caplog.text


def test_otp_connection_error_is_logged_and_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with caplog.at_level(logging.WARNING, logger="whyuseguide_api.timed"):
        assert ask(otp_planner(refuse)) is None
    assert "ConnectError" in caplog.text


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def put(self, key: str, value: str) -> None:
        self.data[key] = value


def test_leg_cache_answers_the_same_question_without_asking_otp() -> None:
    """같은 구간·같은 준비 시각·같은 이동 방식은 한 번만 OTP에 묻는다. 캐시는 좌표로 찾고 id는 다시 붙인다."""
    seen: list[dict[str, Any]] = []
    cache = MemoryCache()

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["variables"])
        return httpx.Response(200, json=plan_body([itinerary(7, 25, [TOEI])]))

    p = OtpPlanner(
        httpx.AsyncClient(transport=httpx.MockTransport(handle)), "http://otp", concurrency=2, cache=cache
    )
    first = ask(p)
    same_spot_other_id = Endpoint("hotel", B.lat, B.lon)
    second = ask(p, b=same_spot_other_id)
    assert first is not None and second is not None and len(seen) == 1
    assert (second.to_id, second.wait_min, second.minutes) == ("hotel", first.wait_min, first.minutes)
    assert ask(p, mode="fast") is not None and len(seen) == 2  # 이동 방식이 다르면 다른 질문

    async def later() -> Leg | None:
        return await p.leg(A, B, DATE, 615, "balance", City(), TOKYO)

    assert anyio.run(later) is not None and len(seen) == 3  # 준비 시각이 다르면 다른 질문


def test_leg_cache_does_not_keep_failures() -> None:
    """OTP가 잠깐 실패한 결과(None)를 넣어 두면 복구된 뒤에도 근사로 남는다. 성공만 넣는다."""
    cache = MemoryCache()
    answers = [httpx.Response(500), httpx.Response(200, json=plan_body([itinerary(7, 25, [TOEI])]))]
    p = OtpPlanner(
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: answers.pop(0))),
        "http://otp",
        concurrency=2,
        cache=cache,
    )
    assert ask(p) is None and cache.data == {}
    assert ask(p) is not None and len(cache.data) == 1


def test_redis_cache_failure_is_logged_and_ignored(caplog: pytest.LogCaptureFixture) -> None:
    """Redis가 죽어도 일정 계산은 계속된다(캐시 없이). 경고는 쏟아내지 않고 한 번만."""

    class DownRedis:
        async def get(self, key: str) -> None:
            raise RedisConnectionError("refused")

        async def set(self, key: str, value: str, ex: int) -> None:
            raise RedisConnectionError("refused")

    cache = RedisLegCache(DownRedis(), ttl_s=60)  # type: ignore[arg-type]
    p = OtpPlanner(
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=plan_body([itinerary(7, 25, [TOEI])]))
            )
        ),
        "http://otp",
        concurrency=2,
        cache=cache,
    )
    with caplog.at_level(logging.WARNING, logger="whyuseguide_api.timed"):
        assert ask(p) is not None and ask(p) is not None
    assert caplog.text.count("구간 캐시") == 1 and "refused" in caplog.text


def test_cache_key_changes_with_city_fares() -> None:
    """운임표가 바뀌면 예전 결과(요금 포함)를 다시 쓰지 않는다."""
    priced = City.model_validate({"fareBands": [{"maxKm": 6, "fare": 178}]})
    assert cache_key(A, B, DATE, 600, "balance", City(), TOKYO) != cache_key(
        A, B, DATE, 600, "balance", priced, TOKYO
    )


def test_day_is_computed_once_when_it_still_matches_the_first_plan() -> None:
    """처음 코스와 같으면 비교용 계산을 따로 하지 않는다(OTP 호출이 절반). 달라졌으면 둘 다 계산한다."""
    from datetime import date as date_
    from datetime import time as time_

    from whyuseguide_api.db.models import TripDay
    from whyuseguide_api.itinerary import DayInput, Routing, compute_day

    row = TripDay(
        day_index=0, date=date_(2026, 10, 27), start_time=time_(9, 0), version=1, cur_index=-1,
        plan_mode="balance",
    )  # fmt: skip
    current = three_stops()

    def run(baseline: Day | None) -> tuple[Any, int]:
        planner = EveryTenMinutes()
        out = anyio.run(compute_day, DayInput(row, current, baseline, False), Routing(planner, TOKYO))
        return out, len(planner.calls)

    same, calls_same = run(current.model_copy())
    _, calls_alone = run(None)
    assert calls_same == calls_alone
    assert same.baseline is not None and same.baseline.end == same.day.totals.end

    reordered = current.model_copy(update={"items": current.items[::-1]})
    changed, calls_changed = run(reordered)
    assert calls_changed > calls_same and changed.baseline is not None


def test_fare_adds_operator_fares_and_fills_gaps_with_the_city_table() -> None:
    """운임표가 있는 운영사는 그 요금(두 구간에 걸친 요금은 한 번만), 없는 구간은 거리 구간값."""
    city = City.model_validate({"fareBands": [{"maxKm": 6, "fare": 178}, {"maxKm": 11, "fare": 209}]})
    start = ready_ms(DATE, 600, TOKYO.tz)

    def fare(it: dict[str, Any]) -> int:
        return to_leg(it, A, FAR, "balance", 600, start, city).fare

    # 메트로 2구간이 요금 하나(환승 통산) + 도에이 220엔
    assert fare(itinerary(0, 30, [TOEI, "メトロ", "メトロ"], 2, [("t", 220), ("m", 178), ("m", 178)])) == 398
    # JR 구간은 운임표가 없다 → 그 구간 2km의 구간 운임 178엔으로 채운다
    assert fare(itinerary(0, 30, [TOEI, JR], 1, [("t", 220), None])) == 220 + 178  # type: ignore[list-item]
    assert fare(itinerary(0, 20, [])) == 0  # 걷기


def test_walks_longer_than_the_limit_are_not_offered() -> None:
    """OTP가 걷는 게 낫다고 해도 60분 넘는 걷기는 안내하지 않는다(근사로 둔다). 60분까지는 쓴다."""
    assert ask(planner([itinerary(0, 89, [])], "WALKING_BETTER_THAN_TRANSIT"), b=FAR) is None
    assert ask(planner([itinerary(0, 89, [])]), b=FAR, mode="fast") is None  # 빠르게: 걷기가 유일한 후보
    ok = ask(planner([itinerary(0, 60, [])], "WALKING_BETTER_THAN_TRANSIT"), b=FAR)
    assert ok is not None and (ok.mode, ok.minutes) == ("walk", 60)
