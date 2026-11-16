"""엔진 공통 픽스처(/engine-fixtures/*.json)를 Python 구현으로 만든다.

Python·TS 두 구현이 같은 입력에 같은 결과를 내는지 확인하는 데 쓴다(PRD §5, P2 완료 기준).
손으로 검증한 시나리오(tests/와 같은 입력) + 고정 시드 무작위 시나리오(좌표 기반 근사 경로까지 거친다).

    uv run python services/engine/scripts/make_fixtures.py
"""

import json
import random
import shutil
from pathlib import Path
from typing import Any

from itinerary_engine.fixtures import run

OUT = Path(__file__).resolve().parents[3] / "engine-fixtures"

CITY = {
    "lineLinks": [["G", "M"], ["M", "H"], ["H", "A"], ["Y", "G"], ["Y", "M"], ["Y", "H"]],
    "fareBands": [
        {"maxKm": 6, "fare": 180},
        {"maxKm": 11, "fare": 210},
        {"maxKm": 19, "fare": 260},
        {"maxKm": 99, "fare": 330},
    ],
    "taxi": {"base": 500, "baseKm": 1.096, "perKm": 400},
    "fuelPerKm": 12,
}
LINES = ["G", "M", "H", "A", "Y", "O"]
TYPES = ["박물관", "신사·사찰", "맛집", "공원", "카페 거리", "전망", "시장"]


def place(pid: str, **kw: Any) -> dict[str, Any]:
    return {"id": pid, "name": pid, "type": "박물관", "lat": 35.68, "lon": 139.70, "defaultStayMin": 60, **kw}


def leg(a: str, b: str, minutes: int, mode: str = "transit") -> dict[str, Any]:
    return {
        "from": a,
        "to": b,
        "planMode": "balance",
        "mode": mode,
        "minutes": minutes,
        "fare": 180,
        "km": 3.0,
    }


def all_legs(ids: list[str], minutes: int) -> list[dict[str, Any]]:
    return [leg(a, b, minutes) for a in ids for b in ids if a != b]


def day(
    items: list[dict[str, Any]], places: list[dict[str, Any]], legs: list[dict[str, Any]], **kw: Any
) -> dict[str, Any]:
    return {
        "date": "2026-10-27",
        "start": "09:00",
        "items": items,
        "places": {p["id"]: p for p in places},
        "legs": legs,
        **kw,
    }


def hand_cases() -> list[tuple[str, str, dict[str, Any]]]:
    """tests/의 손 계산 시나리오와 같은 입력."""
    cases: list[tuple[str, str, dict[str, Any]]] = []
    cases.append(
        (
            "compute-basic",
            "compute",
            {
                "day": day(
                    [{"placeId": "A"}, {"placeId": "B", "stayMin": 90}],
                    [
                        place("A", open="09:00", costPerPerson=500),
                        place("B", open="10:00", costPerPerson=1000),
                    ],
                    [leg("A", "B", 13)],
                )
            },
        )
    )
    for name, fixed, minutes in (
        ("early", "11:00", 10),
        ("within-tolerance", "10:00", 10),
        ("late", "10:00", 13),
    ):
        cases.append(
            (
                f"compute-fixed-{name}",
                "compute",
                {
                    "day": day(
                        [{"placeId": "A"}, {"placeId": "B", "fixedAt": fixed}],
                        [place("A"), place("B")],
                        [leg("A", "B", minutes)],
                    )
                },
            )
        )
    cases.append(
        (
            "compute-open-wait",
            "compute",
            {
                "day": day(
                    [{"placeId": "A"}, {"placeId": "B"}],
                    [place("A"), place("B", open="12:00")],
                    [leg("A", "B", 5)],
                )
            },
        )
    )
    cases.append(
        (
            "compute-closed-last-entry",
            "compute",
            {
                "day": day(
                    [{"placeId": "A"}, {"placeId": "B"}, {"placeId": "C"}],
                    [place("A"), place("B", closed=True), place("C", lastEntry="09:30")],
                    [leg("A", "B", 5), leg("B", "C", 5)],
                )
            },
        )
    )
    cases.append(
        (
            "compute-delay",
            "compute",
            {
                "day": day(
                    [{"placeId": "A"}, {"placeId": "B"}, {"placeId": "C"}],
                    [place("A"), place("B"), place("C")],
                    [leg("A", "B", 10), leg("B", "C", 10)],
                    delay={"gap": 1, "minutes": 20},
                )
            },
        )
    )
    lodging = {"startPoint": {"lat": 35.69, "lon": 139.70}, "endPoint": {"lat": 35.69, "lon": 139.70}}
    cases.append(
        (
            "compute-lodging-car",
            "compute",
            {
                "day": day(
                    [{"placeId": "A"}],
                    [place("A")],
                    [leg("@start", "A", 20, "car"), leg("A", "@end", 30, "car")],
                    transport="car",
                    **lodging,
                )
            },
        )
    )
    cases.append(
        (
            "compute-car-break",
            "compute",
            {
                "day": day(
                    [{"placeId": "A"}, {"placeId": "B"}, {"placeId": "C"}],
                    [place("A", defaultStayMin=10), place("B", defaultStayMin=10), place("C")],
                    [leg("A", "B", 70, "car"), leg("B", "C", 70, "car")],
                    transport="car",
                )
            },
        )
    )
    cases.append(
        (
            "adjust-reorder",
            "autoAdjust",
            {
                "day": day(
                    [{"placeId": "A"}, {"placeId": "B"}],
                    [place("A"), place("B", close="11:00")],
                    all_legs(["A", "B"], 10),
                ),
                "cur": -1,
            },
        )
    )
    cases.append(
        (
            "adjust-trim-booking",
            "autoAdjust",
            {
                "day": day(
                    [{"placeId": "A"}, {"placeId": "C", "fixedAt": "11:30"}],
                    [place("A", defaultStayMin=180, close="12:00"), place("C")],
                    all_legs(["A", "C"], 10),
                ),
                "cur": -1,
            },
        )
    )
    cases.append(
        (
            "adjust-fill-gap",
            "autoAdjust",
            {
                "day": day(
                    [{"placeId": "A", "fixedAt": "09:00"}, {"placeId": "B"}],
                    [place("A", defaultStayMin=30), place("B", open="12:00")],
                    all_legs(["A", "B"], 10),
                ),
                "cur": 0,
            },
        )
    )
    cases.append(
        (
            "adjust-drop",
            "autoAdjust",
            {
                "day": day(
                    [{"placeId": "A"}, {"placeId": "B"}, {"placeId": "C"}],
                    [
                        place("A", defaultStayMin=300, rating=4.5),
                        place("B", defaultStayMin=300, rating=3.1),
                        place("C", defaultStayMin=300, rating=4.0),
                    ],
                    all_legs(["A", "B", "C"], 30),
                    dayEnd="15:00",
                ),
                "cur": -1,
            },
        )
    )
    cases.append(
        (
            "suggest-between",
            "suggest",
            {
                "day": day(
                    [{"placeId": "A"}, {"placeId": "B", "fixedAt": "11:00"}],
                    [place("A"), place("B")],
                    [
                        leg("A", "B", 10),
                        leg("A", "X", 5),
                        leg("X", "B", 5),
                        leg("A", "Y", 20),
                        leg("Y", "B", 20),
                        leg("A", "Z", 5),
                        leg("Z", "B", 5),
                    ],
                ),
                "mode": "between",
                "position": 1,
                "cur": -1,
                "candidates": [
                    place("Y", rating=4.5),
                    place("Z", rating=3.0, closed=True),
                    place("X", defaultStayMin=30, rating=4.0),
                ],
            },
        )
    )
    return cases


def random_place(rng: random.Random, pid: str) -> dict[str, Any]:
    p: dict[str, Any] = {
        "id": pid,
        "name": f"장소{pid}",
        "type": rng.choice(TYPES),
        "lat": round(35.65 + rng.random() * 0.08, 5),
        "lon": round(139.68 + rng.random() * 0.14, 5),
        "defaultStayMin": rng.choice([30, 45, 60, 75, 90, 120]),
        "rating": round(3.0 + rng.random() * 2, 1),
        "costPerPerson": rng.choice([0, 0, 500, 1000, 2000]),
        "indoor": rng.random() < 0.4,
        # 노선 id → 가장 가까운 역까지 km. 가끔 역이 먼 장소(빈 값)도 섞는다.
        "stations": {
            ln: round(0.05 + rng.random() * 0.75, 3)
            for ln in sorted(rng.sample(LINES, rng.choice([0, 1, 1, 2])))
        },
    }
    roll = rng.random()
    if roll < 0.5:
        p["open"], p["close"] = rng.choice(
            [("09:00", "17:00"), ("10:00", "20:00"), ("11:00", "22:00"), ("09:30", "16:30")]
        )
    elif roll < 0.6:
        p["open"], p["close"], p["lastEntry"] = "09:00", "17:00", "16:30"
    elif roll < 0.65:
        p["closed"] = True
    return p


def random_cases(seed: int, count: int) -> list[tuple[str, str, dict[str, Any]]]:
    rng = random.Random(seed)
    cases: list[tuple[str, str, dict[str, Any]]] = []
    kinds = ["compute", "autoAdjust", "suggest"]
    for n in range(count):
        kind = kinds[n % 3]
        size = rng.randint(3, 7) if kind != "suggest" else rng.randint(2, 5)
        places = [random_place(rng, f"{n}-{i}") for i in range(size)]
        items: list[dict[str, Any]] = [{"placeId": p["id"]} for p in places]
        if rng.random() < 0.3:
            items[rng.randrange(1, size)]["fixedAt"] = rng.choice(["11:00", "13:30", "15:00", "17:00"])
        if rng.random() < 0.3:
            items[rng.randrange(size)]["stayMin"] = rng.choice([30, 90, 150])
        d: dict[str, Any] = {
            "date": "2026-10-27",
            "start": rng.choice(["08:30", "09:00", "10:00"]),
            "items": items,
            "places": {p["id"]: p for p in places},
            "city": CITY,
            "planMode": rng.choice(["fast", "balance", "cheap"]),
        }
        if rng.random() < 0.2:
            d["delay"] = {"gap": rng.randrange(1, size), "minutes": rng.choice([10, 20, 40])}
        if rng.random() < 0.2:
            d["startPoint"] = {"lat": 35.69, "lon": 139.70}
            d["endPoint"] = {"lat": 35.69, "lon": 139.70}
        if rng.random() < 0.1:
            d["transport"] = "car"
        if rng.random() < 0.3:
            d["dayEnd"] = rng.choice(["16:00", "18:00", "20:00"])
        payload: dict[str, Any] = {"day": d}
        if kind == "autoAdjust":
            payload["cur"] = rng.choice([-1, -1, 0, 1])
        if kind == "suggest":
            mode = rng.choice(["between", "replace", "fill"])
            payload["mode"] = mode
            payload["cur"] = -1
            payload["position"] = rng.randrange(size) if mode == "replace" else rng.randrange(1, size + 1)
            payload["candidates"] = [random_place(rng, f"{n}-c{i}") for i in range(rng.randint(3, 6))]
        cases.append((f"random-{n:02d}-{kind}", kind, payload))
    return cases


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    cases = hand_cases() + random_cases(seed=20261026, count=45)
    for name, kind, payload in cases:
        fixture = {"name": name, "kind": kind, "input": payload, "expected": run(kind, payload)}
        (OUT / f"{name}.json").write_text(
            json.dumps(fixture, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    print(f"wrote {len(cases)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
