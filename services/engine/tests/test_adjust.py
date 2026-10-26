from builders import all_legs, day, place, stops

from itinerary_engine.adjust import auto_adjust, particle
from itinerary_engine.compute import compute


def test_particle() -> None:
    assert particle("센소지", "을", "를") == "를"
    assert particle("도쿄 국립박물관", "을", "를") == "을"
    assert particle("Tokyo", "을", "를") == "을(를)"


def test_nothing_to_do() -> None:
    d = day([{"placeId": "A"}, {"placeId": "B"}], [place("A"), place("B")], all_legs(["A", "B"], 10))
    r = auto_adjust(d)
    assert not r.applied and r.messages == () and r.items == d.items


def test_reorder_fixes_closing_time() -> None:
    # B는 11:00에 닫는다. A → B 순서면 B가 10:10에 시작해 11:10에 끝나 마감을 넘는다.
    places = [place("A"), place("B", close="11:00")]
    d = day([{"placeId": "A"}, {"placeId": "B"}], places, all_legs(["A", "B"], 10))
    assert compute(d).totals.issues == 1
    r = auto_adjust(d)
    assert [it.place_id for it in r.items] == ["B", "A"]
    assert r.result.totals.issues == 0
    assert r.messages[0] == "순서를 바꿨어요: B을(를) 1번째로, A을(를) 2번째로"  # 한글이 아니면 조사를 둘 다
    assert r.messages[-1] == f"조정 후 {r.result.totals.end} 종료 · 예약과 영업시간 모두 맞아요"
    assert [it.place_id for it in r.undo_items] == ["A", "B"]


def test_trim_before_late_booking_when_reordering_cannot_help() -> None:
    # A는 12:00에 닫아 먼저 가야 하고, C 예약 11:30에 늦는다(A 180분 → 12:10 도착).
    # C를 먼저 가면 A가 마감에 걸려 문제 수가 같으므로 순서는 그대로 두고 A 체류를 줄인다.
    places = [place("A", defaultStayMin=180, close="12:00"), place("C")]
    d = day([{"placeId": "A"}, {"placeId": "C", "fixedAt": "11:30"}], places, all_legs(["A", "C"], 10))
    assert stops(compute(d))[1].issue == "late"
    r = auto_adjust(d)
    assert [it.place_id for it in r.items] == ["A", "C"]
    assert r.result.totals.issues == 0
    assert stops(r.result)[1].arrive == "11:40"  # 예약 + 허용 10분 안
    assert r.messages == (
        "A 체류 180분 → 150분",
        f"조정 후 {r.result.totals.end} 종료 · 예약과 영업시간 모두 맞아요",
    )


def test_trim_longest_stay_for_day_end() -> None:
    # 종료 목표 14:00. A 240 + 이동 10 + B 120 → 15:10 종료. 가장 긴 A를 15분씩 줄인다.
    places = [place("A", defaultStayMin=240), place("B", defaultStayMin=120)]
    d = day([{"placeId": "A"}, {"placeId": "B"}], places, all_legs(["A", "B"], 10), dayEnd="14:00")
    r = auto_adjust(d)
    assert r.result.totals.end == "13:55"
    assert "A 체류 240분 → 165분" in r.messages


def test_open_wait_only_suggests_filling_the_gap() -> None:
    # 고정 예약 때문에 순서를 바꿀 수 없고, B 오픈까지 대기가 길다.
    places = [place("A", defaultStayMin=30), place("B", open="12:00")]
    d = day([{"placeId": "A", "fixedAt": "09:00"}, {"placeId": "B"}], places, all_legs(["A", "B"], 10))
    r = auto_adjust(d, cur=0)
    assert r.fill_gap is not None
    assert (r.fill_gap.position, r.fill_gap.wait_min) == (1, 140)  # 09:40 도착, 12:00 오픈
    assert r.drop_suggestion is None
    assert r.messages[-1] == "B 오픈까지 140분이 비어요"


def test_unfixable_day_suggests_dropping_lowest_rated() -> None:
    places = [
        place("A", defaultStayMin=300, rating=4.5),
        place("B", defaultStayMin=300, rating=3.1),
        place("C", defaultStayMin=300, rating=4.0),
    ]
    d = day(
        [{"placeId": "A"}, {"placeId": "B"}, {"placeId": "C"}],
        places,
        all_legs(["A", "B", "C"], 30),
        dayEnd="15:00",
    )
    r = auto_adjust(d)
    assert r.drop_suggestion is not None and r.drop_suggestion.place_id == "B"
    assert "B 빼기를 제안해요" in r.messages[-1]
    # 체류는 하한(30분, 원래의 50% = 150분)까지만 줄인다
    assert all(it.stay_min is None or it.stay_min >= 150 for it in r.items)


def test_visited_places_are_not_touched() -> None:
    places = [place("A", close="23:00"), place("B", close="11:30"), place("C")]
    d = day([{"placeId": "A"}, {"placeId": "C"}, {"placeId": "B"}], places, all_legs(["A", "B", "C"], 10))
    r = auto_adjust(d, cur=0)
    assert r.items[0].place_id == "A"  # 지금 있는 곳은 그대로
    assert [it.place_id for it in r.items][1:] == ["B", "C"]


def test_delay_messages() -> None:
    places = [place("A"), place("B"), place("C")]
    legs = all_legs(["A", "B", "C"], 10)
    pushed = day(
        [{"placeId": "A"}, {"placeId": "B"}, {"placeId": "C"}], places, legs, delay={"gap": 1, "minutes": 15}
    )
    assert auto_adjust(pushed).messages[0] == "뒤 일정이 15분씩 밀렸어요"
    absorbed = day(
        [{"placeId": "A"}, {"placeId": "B", "fixedAt": "11:00"}],
        places,
        legs,
        delay={"gap": 1, "minutes": 15},
    )
    assert auto_adjust(absorbed).messages[0] == "15분 지연은 예약 전 대기 시간으로 흡수돼요"
