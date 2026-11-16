import pytest

from itinerary_engine.feedback import CourseRating, apply_rating
from itinerary_engine.generate import Prefs, Profile


def test_tags_change_prefs_and_explain() -> None:
    r = apply_rating(
        Prefs(),
        CourseRating(stars=4, tags=("long_moves", "too_tight", "local_good", "long_queue")),
        {},
        [],
    )
    assert r.prefs.move_cap_factor == 0.8
    assert (r.prefs.place_count_delta, r.prefs.slack_min, r.prefs.mix_shift) == (-1, 10, 15)
    assert r.prefs.booking_first
    assert r.messages == (
        "하루 이동 시간을 20% 줄여요",
        "하루 장소를 1곳 줄이고 장소 사이에 10분 여유를 둬요",
        "로컬 장소를 더 넣어요",
        "예약할 수 있는 곳은 예약 버튼을 먼저 보여줘요",
    )
    assert not r.verify_routes


def test_effects_accumulate_over_days() -> None:
    once = apply_rating(Prefs(), CourseRating(stars=4, tags=("long_moves",)), {}, []).prefs
    twice = apply_rating(once, CourseRating(stars=4, tags=("long_moves",)), {}, []).prefs
    assert twice.move_cap_factor == 0.64


def test_votes_shift_tag_weights() -> None:
    profiles = {"A": Profile(tags={"역사·문화": 1.0, "사진 명소": 0.5}), "B": Profile(tags={"쇼핑": 1.0})}
    r = apply_rating(Prefs(), CourseRating(stars=4, votes={"A": 1, "B": -1}), profiles, [])
    assert r.prefs.tag_weights == {"역사·문화": 0.1, "사진 명소": 0.1, "쇼핑": -0.1}
    assert "좋았던 1곳과 비슷한 장소를 더 넣어요" in r.messages
    assert "별로였던 1곳과 비슷한 장소는 줄여요" in r.messages


def test_low_stars_avoid_todays_types_and_bad_accuracy_requests_check() -> None:
    r = apply_rating(
        Prefs(),
        CourseRating(stars=2, tags=("crowded",), routeAccuracy="off"),
        {},
        ["박물관", "맛집", "박물관"],
    )
    assert r.prefs.avoid_types == ("맛집", "박물관")
    assert r.prefs.crowd_avoid and r.prefs.mix_shift == 10
    assert r.verify_routes
    assert r.messages[-1] == "오늘 다닌 구간의 노선·시간을 다시 확인할게요"


def test_unknown_tag_is_rejected_and_neutral_tags_change_nothing() -> None:
    """모르는 태그는 조용히 무시하지 않는다. 반영 규칙이 없는 태그(동선이 효율적, 시간 안내 정확)는 그대로."""
    with pytest.raises(ValueError, match="tags"):
        CourseRating.model_validate({"stars": 4, "tags": ["이동이 길었어요"]})
    r = apply_rating(Prefs(), CourseRating(stars=5, tags=("efficient_route", "accurate_times")), {}, [])
    assert r.prefs == Prefs() and r.messages == ()
