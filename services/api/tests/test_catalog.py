from conftest import needs_db, place_id
from fastapi.testclient import TestClient
from sqlalchemy import Engine

pytestmark = needs_db


def test_guest_token(client: TestClient, token: str) -> None:
    assert len(token) >= 40
    r = client.post("/v1/auth/guest")
    assert r.status_code == 200
    body = r.json()
    assert body["isGuest"] is True and body["expiresAt"]


def test_cities(client: TestClient) -> None:
    r = client.get("/v1/cities")
    assert r.status_code == 200
    tokyo = next(c for c in r.json() if c["code"] == "tokyo")
    assert tokyo == {
        "code": "tokyo",
        "nameKo": "도쿄",
        "timezone": "Asia/Tokyo",
        "currency": "JPY",
        "launched": False,
        "dataTier": "reference",
        "kind": "city",
    }


def test_place_search_shows_only_active(client: TestClient, db: Engine) -> None:
    r = client.get("/v1/places", params={"city": "tokyo", "q": "센소"})
    assert [p["nameKo"] for p in r.json()] == ["센소지"]
    assert client.get("/v1/places", params={"city": "tokyo", "q": "숨김"}).json() == []


def test_place_search_by_type_rejects_unknown_types(client: TestClient) -> None:
    """유형은 constants.json의 placeTypes만 받는다. 오타는 빈 결과가 아니라 422."""
    ok = client.get("/v1/places", params={"city": "tokyo", "type": "박물관"})
    assert ok.status_code == 200 and {p["type"] for p in ok.json()} == {"박물관"}
    bad = client.get("/v1/places", params={"city": "tokyo", "type": "박물 관"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "unknown_type"


def test_place_search_near_is_sorted_by_distance(client: TestClient, db: Engine) -> None:
    r = client.get("/v1/places", params={"city": "tokyo", "near": "35.7148,139.7967", "limit": 3})
    rows = r.json()
    assert rows[0]["nameKo"] == "센소지" and rows[0]["distanceM"] == 0
    assert rows[1]["distanceM"] >= rows[0]["distanceM"]


def test_place_detail_and_errors(client: TestClient, db: Engine) -> None:
    r = client.get(f"/v1/places/{place_id(db, 'tnm')}")
    assert r.status_code == 200
    d = r.json()
    assert d["nameLocal"] == "東京国立博物館" and d["defaultStayMin"] == 90
    assert d["hours"][0] == {"weekday": 0, "open": "09:30", "close": "17:00", "lastEntry": None}

    hidden = client.get(f"/v1/places/{place_id(db, 'hidden')}")
    assert hidden.status_code == 404
    assert hidden.json() == {"error": {"code": "not_found", "message": "장소를 찾을 수 없어요"}}

    bad = client.get("/v1/places", params={"city": "tokyo", "near": "nope"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_request"
