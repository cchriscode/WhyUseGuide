from builders import all_legs, day, leg, place

from itinerary_engine.models import Place
from itinerary_engine.suggest import suggest


def p(pid: str, **kw: object) -> Place:
    return Place.model_validate(place(pid, **kw))


def test_between_ranks_absorbed_candidate_first() -> None:
    # 지금: A 09:00–10:00 → (10분) → B 예약 11:00 (50분 대기), 종료 12:00, 이동 10분
    places = [place("A"), place("B")]
    legs = [
        leg("A", "B", 10),
        leg("A", "X", 5),
        leg("X", "B", 5),
        leg("A", "Y", 20),
        leg("Y", "B", 20),
        leg("A", "Z", 5),
        leg("Z", "B", 5),
    ]
    d = day([{"placeId": "A"}, {"placeId": "B", "fixedAt": "11:00"}], places, legs)
    x = p("X", defaultStayMin=30, rating=4.0)  # 대기 시간 안에 들어간다
    y = p("Y", defaultStayMin=60, rating=4.5)  # B 예약에 늦게 만든다
    z = p("Z", defaultStayMin=60, rating=3.0, closed=True)  # 휴무
    options = suggest(d, "between", [y, z, x], position=1)

    assert [o.place_id for o in options] == ["X", "Z", "Y"]
    ox, oz, oy = options
    # X: 이동 5+5 = 10 → Δ0, 종료 그대로 → 0 − 6×4.0 − 20 = −44
    assert (ox.score, ox.detour_min, ox.absorbed, ox.end_after) == (-44.0, 0, True, "12:00")
    assert ox.badges == ("기다리는 시간 활용", "가는 길")
    assert ox.effect == "이동 그대로 · 끝나는 시간 그대로"
    assert ox.warning is None
    # Z: 영업시간 밖 80 − 6×3.0 = 62, B는 11:10 도착(허용 안)이라 새 문제 없음, 종료 10분 늦어짐
    assert (oz.score, oz.outside_hours, oz.new_issues, oz.warning) == (62.0, True, 0, "영업시간 밖이에요")
    assert oz.effect == "이동 그대로 · 끝나는 시간 10분 늦어져요"
    # Y: Δ30 + 새 문제(B 늦음) 60 − 6×4.5 = 63
    assert (oy.score, oy.detour_min, oy.new_issues) == (63.0, 30, 1)
    assert oy.warning == "다른 일정 1곳이 맞지 않게 돼요"


def test_replace_prefers_same_type() -> None:
    places = [place("A"), place("B", type="cafe"), place("C")]
    ids = ["A", "B", "C", "P", "Q"]
    d = day([{"placeId": "A"}, {"placeId": "B"}, {"placeId": "C"}], places, all_legs(ids, 10))
    pp = p("P", type="cafe", rating=4.0)
    qq = p("Q", type="museum", rating=4.0)
    options = suggest(d, "replace", [qq, pp], position=1)
    assert [o.place_id for o in options] == ["P", "Q"]
    assert options[0].same_type and "비슷한 곳" in options[0].badges
    assert options[0].score - options[1].score == -25
    assert all(o.position == 1 for o in options)


def test_fill_tries_every_gap_after_current_place() -> None:
    # A 09:00–10:00 → B 예약 13:00. X는 A·B 사이(대기 흡수)가 가장 좋고, 맨 뒤는 종료가 늦어진다.
    places = [place("A"), place("B")]
    d = day([{"placeId": "A"}, {"placeId": "B", "fixedAt": "13:00"}], places, all_legs(["A", "B", "X"], 10))
    [best] = suggest(d, "fill", [p("X", rating=4.0)], cur=0, limit=1)
    assert best.position == 1 and best.absorbed


def test_candidates_already_in_day_are_skipped_and_limit_applies() -> None:
    places = [place("A"), place("B")]
    ids = ["A", "B", "C1", "C2", "C3", "C4"]
    d = day([{"placeId": "A"}, {"placeId": "B"}], places, all_legs(ids, 10))
    pool = [p("A"), *(p(f"C{i}", rating=float(i)) for i in range(1, 5))]
    options = suggest(d, "between", pool, position=1)
    assert len(options) == 3
    assert "A" not in {o.place_id for o in options}
    assert [o.place_id for o in options] == ["C4", "C3", "C2"]  # 나머지가 같으면 평점 순
