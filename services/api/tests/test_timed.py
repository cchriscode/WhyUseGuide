"""시간표 구간(열차에 맞춘 출발 시각). OTP는 가짜 HTTP 응답·가짜 시간표로 대신한다(DB 불필요)."""

import json
from typing import Any

import anyio
import httpx

from itinerary_engine.compute import compute
from itinerary_engine.config import to_hhmm, to_min
from itinerary_engine.models import City, Day, Leg, LegRow, PlanMode, StopRow
from itinerary_engine.route import Endpoint
from whyuseguide_api.timed import OtpPlanner, ready_ms, timed_compute, to_leg

DATE = "2026-10-27"
A = Endpoint("A", 35.7148, 139.7967)  # 센소지
B = Endpoint("B", 35.7188, 139.7765)  # 도쿄 국립박물관(경로 약 2.4km → 균형은 대중교통, 알뜰은 도보)
FAR = Endpoint("F", 35.6812, 139.7671)  # 도쿄역(경로 약 6km)
NEAR = Endpoint("N", 35.7119, 139.7963)  # 나카미세(약 300m → 도보)
TOEI, JR = "東京都交通局", "東日本旅客鉄道"


def itinerary(start: float, end: float, agencies: list[str], transfers: int = 0) -> dict[str, Any]:
    """ready(10:00) 기준 분으로 만든 OTP 경로."""
    base = ready_ms(DATE, 600)
    legs: list[dict[str, Any]] = [{"mode": "WALK", "distance": 400.0, "route": None}]
    legs += [{"mode": "SUBWAY", "distance": 2000.0, "route": {"agency": {"name": a}}} for a in agencies]
    return {
        "startTime": base + int(start * 60000),
        "endTime": base + int(end * 60000),
        "numberOfTransfers": transfers,
        "legs": legs,
    }


def planner(
    its: list[dict[str, Any]], seen: list[dict[str, Any]] | None = None, status: int = 200
) -> OtpPlanner:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content)["variables"])
        return httpx.Response(status, json={"data": {"plan": {"itineraries": its}}})

    return OtpPlanner(httpx.AsyncClient(transport=httpx.MockTransport(handle)), "http://otp")


def ask(p: OtpPlanner, b: Endpoint = B, mode: PlanMode = "balance") -> Leg | None:
    async def go() -> Leg | None:
        return await p.leg(A, b, DATE, 600, mode, City(), frozenset({JR}))

    return anyio.run(go)


def test_to_leg_waits_down_and_arrives_up() -> None:
    leg = to_leg(itinerary(7.5, 25.2, [TOEI]), A, B, "balance", 600, ready_ms(DATE, 600), City(), frozenset())
    assert (leg.depart_at, leg.wait_min, leg.minutes) == ("10:00", 7, 19)  # 10:07 출발, 10:26 도착
    assert leg.mode == "transit" and not leg.estimated and leg.km == 2.4


def test_balance_takes_earliest_arrival_and_marks_estimated_lines() -> None:
    its = [itinerary(3, 30, [TOEI]), itinerary(8, 26, [JR], 0), itinerary(0, 35, [])]  # 도보만은 더 늦어 뺀다
    leg = ask(planner(its))
    assert leg is not None and (leg.wait_min, leg.minutes) == (8, 18)
    assert leg.estimated  # 근사 노선(JR)을 탄 경로는 "예상"


def test_balance_walks_when_otp_says_walking_is_better() -> None:
    """도보 기준을 넘어도 OTP가 걷는 게 낫다고 하면 근사 대신 OTP 걷기 경로."""
    body = {
        "data": {
            "plan": {
                "routingErrors": [{"code": "WALKING_BETTER_THAN_TRANSIT"}],
                "itineraries": [itinerary(0, 31, [])],
            }
        }
    }
    p = OtpPlanner(
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))),
        "http://otp",
    )
    leg = ask(p)
    assert leg is not None and (leg.mode, leg.wait_min, leg.minutes, leg.estimated) == ("walk", 0, 31, False)


def test_walk_only_without_reason_falls_back() -> None:
    """정류장은 있는데 그 시각에 버스가 없을 때 등: 이유 없이 걷기 경로만 오면 근사("예상")."""
    for mode in ("balance", "fast", "cheap"):
        assert ask(planner([itinerary(0, 140, [])]), b=FAR, mode=mode) is None


def test_no_stops_in_range_falls_back_instead_of_hours_of_walking() -> None:
    """실데이터: 근처에 역이 없으면 OTP가 215분 걷기 경로를 낸다. 데이터 공백이라 근사("예상")로 둔다."""
    body = {
        "data": {
            "plan": {"routingErrors": [{"code": "NO_STOPS_IN_RANGE"}], "itineraries": [itinerary(0, 215, [])]}
        }
    }
    for mode in ("balance", "fast", "cheap"):
        p = OtpPlanner(
            httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))),
            "http://otp",
        )
        assert ask(p, b=FAR, mode=mode) is None


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
    leg = ask(planner([itinerary(0, 6, [])], seen), b=NEAR)
    assert leg is not None and (leg.mode, leg.wait_min, leg.minutes) == ("walk", 0, 6)
    assert seen[0]["modes"] == [{"mode": "WALK"}] and seen[0]["time"] == "10:00"
    assert ask(planner([], status=500)) is None
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
        estimated_agencies: frozenset[str],
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
    result = anyio.run(timed_compute, three_stops(), p)
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
    result = anyio.run(timed_compute, three_stops(), EveryTenMinutes(fail=frozenset({"Q"})))
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
        estimated_agencies: frozenset[str],
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
    result = anyio.run(timed_compute, day, p)
    legs = [r for r in result.rows if isinstance(r, LegRow)]
    assert [leg.estimated for leg in legs] == [False, False, False]
    rs = [c[2] for c in p.calls if c[:2] == ("R", "S")]
    assert len(rs) == 2 and rs[0] == to_min(legs[2].depart)  # 처음 물은 시각으로 돌아왔다


def test_walking_better_than_transit_asks_for_the_walk() -> None:
    answers = [
        {"data": {"plan": {"routingErrors": [{"code": "WALKING_BETTER_THAN_TRANSIT"}], "itineraries": []}}},
        {"data": {"plan": {"routingErrors": [], "itineraries": [itinerary(0, 29, [])]}}},
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=answers.pop(0))

    p = OtpPlanner(httpx.AsyncClient(transport=httpx.MockTransport(handle)), "http://otp")
    leg = ask(p, b=FAR)
    assert leg is not None and (leg.mode, leg.minutes) == ("walk", 29) and not answers
