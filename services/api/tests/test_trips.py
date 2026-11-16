from typing import Any

import pytest
from conftest import auth, needs_db, place_id
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from itinerary_engine.generate import GenerateInput, GenerateResult, generate
from whyuseguide_api.routers import ratings

pytestmark = needs_db

TRIP = {
    "city": "tokyo",
    "startDate": "2026-10-27",
    "days": 2,
    "adults": 2,
    "tastes": ["역사·문화"],
    "pace": "relaxed",
    "mix": 30,
}


def create(client: TestClient, token: str, **over: Any) -> dict[str, Any]:
    r = client.post("/v1/trips", json={**TRIP, **over}, headers=auth(token))
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


def stops(day: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in day["day"]["rows"] if r["kind"] == "stop"]


def test_create_returns_three_plans(client: TestClient, token: str) -> None:
    body = create(client, token)
    assert set(body["plans"]) == {"fast", "balance", "cheap"}
    for plan in body["plans"].values():
        assert len(plan["days"]) == 2
        assert plan["summary"]["moveMin"] == sum(d["totals"]["moveMin"] for d in plan["days"])
    places = [
        {r["placeId"] for r in d["rows"] if r["kind"] == "stop"} for d in body["plans"]["balance"]["days"]
    ]
    assert all(places) and not places[0] & places[1]  # 날짜마다 다른 장소
    legs = [r for d in body["plans"]["balance"]["days"] for r in d["rows"] if r["kind"] == "leg"]
    assert all(leg["estimated"] for leg in legs)  # 테스트는 OTP를 끈다: 엔진 근사 → "예상"
    assert all(leg["label"] for leg in legs)


def test_rows_follow_train_times_when_otp_is_on(client: TestClient, token: str) -> None:
    """OTP 시간표 구간(방식 B): 가짜 시간표(매 10분 열차, 역까지 3분, 타고 15분)로 생성·조회 모두 확인."""
    from test_timed import EveryTenMinutes

    app = client.app
    app.state.planner = EveryTenMinutes()  # type: ignore[attr-defined]
    try:
        body = create(client, token)
        trip_id = body["tripId"]
        rendered = client.get(f"/v1/trips/{trip_id}", headers=auth(token)).json()["days"][0]["day"]["rows"]
        for rows in (body["plans"]["balance"]["days"][0]["rows"], rendered):
            pairs = [(rows[i], rows[i + 1]) for i in range(len(rows) - 1) if rows[i]["kind"] == "leg"]
            assert pairs
            for leg, stop in pairs:
                assert not leg["estimated"] and leg["depart"].endswith("7") and leg["minutes"] == 18
                h, m = map(int, leg["depart"].split(":"))
                assert stop["arrive"] == f"{(h * 60 + m + 18) // 60:02d}:{(m + 18) % 60:02d}"
    finally:
        app.state.planner = None  # type: ignore[attr-defined]


def test_get_select_and_auth(client: TestClient, token: str) -> None:
    trip_id = create(client, token)["tripId"]
    r = client.get(f"/v1/trips/{trip_id}", headers=auth(token))
    assert r.status_code == 200
    days = r.json()["days"]
    assert [d["version"] for d in days] == [1, 1]
    assert days[0]["baseline"] is not None and days[0]["day"]["planMode"] == "balance"

    selected = client.post(
        f"/v1/trips/{trip_id}/select-plan", json={"planMode": "cheap"}, headers=auth(token)
    ).json()
    assert selected["planMode"] == "cheap"
    assert {d["day"]["planMode"] for d in selected["days"]} == {"cheap"}
    assert [d["version"] for d in selected["days"]] == [2, 2]

    assert client.get(f"/v1/trips/{trip_id}").status_code == 401
    other = client.post("/v1/auth/guest").json()["token"]
    assert client.get(f"/v1/trips/{trip_id}", headers=auth(other)).status_code == 404


def test_patch_remove_and_version_conflict(client: TestClient, token: str) -> None:
    trip_id = create(client, token)["tripId"]
    day = client.get(f"/v1/trips/{trip_id}", headers=auth(token)).json()["days"][0]
    before = [s["placeId"] for s in stops(day)]
    r = client.patch(
        f"/v1/trips/{trip_id}/days/0",
        json={"ops": [{"op": "remove", "position": 1}], "clientVersion": day["version"]},
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    after = r.json()
    assert [s["placeId"] for s in stops(after)] == before[:1] + before[2:]
    assert after["version"] == day["version"] + 1
    assert after["adjust"] is not None and after["baseline"] is not None

    stale = client.patch(
        f"/v1/trips/{trip_id}/days/0",
        json={"ops": [{"op": "remove", "position": 1}], "clientVersion": day["version"]},
        headers=auth(token),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"
    assert stale.json()["latest"]["version"] == after["version"]


def test_auto_adjust_and_undo(client: TestClient, token: str, db: Engine) -> None:
    trip_id = create(client, token, dayEndTime="15:00")["tripId"]
    day = client.get(f"/v1/trips/{trip_id}", headers=auth(token)).json()["days"][0]
    first = stops(day)[0]["placeId"]
    # 첫 장소 체류를 크게 늘려 종료 목표(15:00)를 넘게 만든다. 순서만 바꿔서는 못 맞추므로 체류를 줄인다
    r = client.patch(
        f"/v1/trips/{trip_id}/days/0",
        json={"ops": [{"op": "stay", "placeId": first, "stayMin": 240}], "clientVersion": day["version"]},
        headers=auth(token),
    ).json()
    assert r["adjust"]["applied"] is True
    assert r["adjust"]["undoAvailable"] is True
    assert any("체류" in m for m in r["adjust"]["messages"])
    trimmed = next(s for s in stops(r) if s["placeId"] == first)["stayMin"]
    assert trimmed < 240

    undone = client.post(f"/v1/trips/{trip_id}/days/0/undo", headers=auth(token))
    assert undone.status_code == 200
    assert next(s for s in stops(undone.json()) if s["placeId"] == first)["stayMin"] == 240
    again = client.post(f"/v1/trips/{trip_id}/days/0/undo", headers=auth(token))
    assert again.status_code == 409


def test_suggest_and_checkin(client: TestClient, token: str, db: Engine) -> None:
    trip_id = create(client, token)["tripId"]
    day = client.get(f"/v1/trips/{trip_id}", headers=auth(token)).json()["days"][0]
    in_day = {s["placeId"] for s in stops(day)}

    s = client.post(
        f"/v1/trips/{trip_id}/days/0/suggest", json={"mode": "between", "position": 1}, headers=auth(token)
    )
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["title"].endswith("사이") and 1 <= len(body["options"]) <= 3
    assert not {o["placeId"] for o in body["options"]} & in_day
    assert body["options"] == sorted(body["options"], key=lambda o: (o["score"], o["placeId"]))

    c = client.post(f"/v1/trips/{trip_id}/days/0/checkin", json={"position": 0}, headers=auth(token)).json()
    assert c["day"]["curIndex"] == 0
    locked = client.patch(
        f"/v1/trips/{trip_id}/days/0",
        json={"ops": [{"op": "remove", "position": 0}], "clientVersion": c["version"]},
        headers=auth(token),
    )
    assert locked.status_code == 422 and locked.json()["error"]["code"] == "invalid_position"

    inserted = client.patch(
        f"/v1/trips/{trip_id}/days/0",
        json={
            "ops": [{"op": "insert", "position": 1, "placeId": body["options"][0]["placeId"]}],
            "autoAdjust": False,
            "clientVersion": c["version"],
        },
        headers=auth(token),
    ).json()
    assert stops(inserted)[1]["placeId"] == body["options"][0]["placeId"]


def test_fixed_booking_and_validation(client: TestClient, token: str, db: Engine) -> None:
    tnm = place_id(db, "tnm")
    body = create(client, token, fixedBookings=[{"placeId": tnm, "date": "2026-10-28", "time": "14:00"}])
    day2 = body["plans"]["balance"]["days"][1]
    row = next(r for r in day2["rows"] if r["kind"] == "stop" and r["placeId"] == tnm)
    assert row["fixedAt"] == "14:00" and row["start"] == "14:00"

    bad_city = client.post("/v1/trips", json={**TRIP, "city": "osaka"}, headers=auth(token))
    assert bad_city.status_code == 422 and bad_city.json()["error"]["code"] == "unknown_city"
    no_taste = client.post("/v1/trips", json={**TRIP, "tastes": []}, headers=auth(token))
    assert no_taste.status_code == 422 and no_taste.json()["error"]["code"] == "invalid_request"


def test_suggest_rejects_positions_outside_the_remaining_day(client: TestClient, token: str) -> None:
    """음수 위치는 파이썬 슬라이스로 엉뚱한 일정이 되고, 범위를 넘으면 500이 났다. 둘 다 422로 막는다."""
    trip_id = create(client, token)["tripId"]
    day = client.get(f"/v1/trips/{trip_id}", headers=auth(token)).json()["days"][0]
    count = len(stops(day))
    url = f"/v1/trips/{trip_id}/days/0/suggest"
    for body in (
        {"mode": "replace", "position": -1},
        {"mode": "replace", "position": count},
        {"mode": "between", "position": count + 1},
        {"mode": "replace"},
    ):
        r = client.post(url, json=body, headers=auth(token))
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_position", (body, r.text)
    assert (
        client.post(url, json={"mode": "between", "position": count}, headers=auth(token)).status_code == 200
    )
    client.post(f"/v1/trips/{trip_id}/days/0/checkin", json={"position": 0}, headers=auth(token))
    visited = client.post(url, json={"mode": "replace", "position": 0}, headers=auth(token))
    assert visited.status_code == 422  # 다녀온 곳은 바꿀 수 없다


def test_next_day_regeneration_keeps_lodging_and_clears_undo(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """평가 뒤 다음 날을 다시 만들 때도 숙소에서 출발하고, 다시 만들기 전의 되돌리기는 남지 않는다."""
    lodging = {"lat": 35.7101, "lon": 139.8107}
    trip_id = create(client, token, lodging=lodging)["tripId"]
    days = client.get(f"/v1/trips/{trip_id}", headers=auth(token)).json()["days"]
    assert days[1]["day"]["rows"][0]["from"] == "@start"
    # 다음 날에 자동 조정 스냅샷을 만든다(되돌리기 가능 상태)
    patched = client.patch(
        f"/v1/trips/{trip_id}/days/1",
        json={"ops": [{"op": "delay", "gap": 1, "minutes": 240}], "clientVersion": days[1]["version"]},
        headers=auth(token),
    ).json()
    assert patched["adjust"]["applied"] and patched["adjust"]["undoAvailable"]

    inputs: list[GenerateInput] = []

    def spy(gen: GenerateInput) -> GenerateResult:
        inputs.append(gen)
        return generate(gen)

    monkeypatch.setattr(ratings, "generate", spy)
    r = client.post(
        "/v1/course-ratings",
        json={"tripId": trip_id, "dayIndex": 0, "stars": 4, "tags": ["too_tight"]},
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    assert len(inputs) == 1 and inputs[0].start_point is not None and inputs[0].end_point is not None
    assert (inputs[0].start_point.lat, inputs[0].start_point.lon) == pytest.approx((35.7101, 139.8107))
    nxt = r.json()["nextDay"]
    assert nxt["day"]["rows"][0]["from"] == "@start" and nxt["day"]["rows"][-1]["to"] == "@end"
    undo = client.post(f"/v1/trips/{trip_id}/days/1/undo", headers=auth(token))
    assert undo.status_code == 409 and undo.json()["error"]["code"] == "nothing_to_undo"


def test_course_rating_updates_prefs_and_next_day(client: TestClient, token: str) -> None:
    trip_id = create(client, token)["tripId"]
    days = client.get(f"/v1/trips/{trip_id}", headers=auth(token)).json()["days"]
    first_place = stops(days[0])[0]["placeId"]
    r = client.post(
        "/v1/course-ratings",
        json={
            "tripId": trip_id,
            "dayIndex": 0,
            "stars": 4,
            "tags": ["too_tight"],
            "votes": {first_place: 1},
            "routeAccuracy": "off",
        },
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "하루 장소를 1곳 줄이고 장소 사이에 10분 여유를 둬요" in body["messages"]
    assert body["prefs"]["placeCountDelta"] == -1 and body["prefs"]["slackMin"] == 10
    assert body["verifyRoutes"] is True
    nxt = body["nextDay"]
    assert nxt is not None and nxt["dayIndex"] == 1
    assert len(stops(nxt)) == len(stops(days[1])) - 1  # 하루 장소 1곳 줄었다
    assert not {s["placeId"] for s in stops(nxt)} & {s["placeId"] for s in stops(days[0])}
    assert nxt["version"] == days[1]["version"] + 1

    bad = client.post(
        "/v1/course-ratings",
        json={"tripId": trip_id, "dayIndex": 0, "stars": 3, "votes": {stops(days[1])[0]["placeId"]: -1}},
        headers=auth(token),
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_vote"

    # 같은 날을 다시 평가하면 반영이 두 번 되지 않게 409. 선호값은 그대로다.
    again = client.post(
        "/v1/course-ratings",
        json={"tripId": trip_id, "dayIndex": 0, "stars": 4, "tags": ["too_tight"]},
        headers=auth(token),
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_rated"
    after = client.get(f"/v1/trips/{trip_id}", headers=auth(token)).json()["days"][1]
    assert after["version"] == nxt["version"] and len(stops(after)) == len(stops(nxt))

    # 모르는 태그(예전의 한국어 문구 포함)는 조용히 무시하지 않고 422
    unknown = client.post(
        "/v1/course-ratings",
        json={"tripId": trip_id, "dayIndex": 1, "stars": 4, "tags": ["일정이 빡빡했어요"]},
        headers=auth(token),
    )
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "invalid_request"
