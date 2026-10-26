from typing import Any

from conftest import auth, needs_db, place_id
from fastapi.testclient import TestClient
from sqlalchemy import Engine

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
    assert all(leg["estimated"] for leg in legs)  # OTP 연동 전: 엔진 근사 → "예상"
    assert all(leg["label"] for leg in legs)


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
    trip_id = create(client, token, dayEndTime="16:00")["tripId"]
    day = client.get(f"/v1/trips/{trip_id}", headers=auth(token)).json()["days"][0]
    first = stops(day)[0]["placeId"]
    # 첫 장소 체류를 크게 늘려 종료 목표(16:00)를 넘게 만든다 → 자동 조정이 체류를 줄인다
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
