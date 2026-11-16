import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, select
from support import conn as conn
from support import needs_db

from whyuseguide_api.db.models import Place, PlaceHours, PlaceStatus
from whyuseguide_pipelines.place_reviews import PlaceReview, ReviewFile, apply_reviews, read_reviews
from whyuseguide_pipelines.places import PlaceCandidate, load_places

SOURCE = "test-reviews"


def review(ref: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ref": ref,
        "name_local": ref,
        "verdict": "open",
        "confidence": "high",
        "hours": [{"days": ["tue", "sun"], "open": "09:30", "close": "17:00", "last_entry": "16:30"}],
    }
    return base | kw


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({}, PlaceStatus.active),
        ({"hours": "always"}, PlaceStatus.active),
        ({"confidence": "medium"}, PlaceStatus.active),
        ({"confidence": "low"}, PlaceStatus.hidden),  # 확인이 부족하면 공개하지 않는다
        ({"hours": None}, PlaceStatus.hidden),  # 여는 시간을 모르면 밤에도 일정에 들어갈 수 있다
        ({"excluded": "추천에서 뺌"}, PlaceStatus.hidden),
        ({"verdict": "closed_permanently"}, PlaceStatus.closed),
        ({"verdict": "temporarily_closed"}, PlaceStatus.hidden),
        ({"verdict": "moved"}, PlaceStatus.hidden),
        ({"verdict": "outside_tokyo"}, PlaceStatus.hidden),
        ({"verdict": "not_a_visitor_place"}, PlaceStatus.hidden),
    ],
)
def test_status_follows_the_review(fields: dict[str, Any], status: PlaceStatus) -> None:
    assert PlaceReview.model_validate(review("A", **fields)).status() == status


def test_review_rejects_unknown_types_fields_and_bad_times() -> None:
    for bad in (
        {"type": "박물 관"},
        {"hours": [{"days": ["tue"], "open": "9:30", "close": "17:00"}]},
        {"hours": [{"days": ["holiday"], "open": "09:30", "close": "17:00"}]},
        {"opening": "always"},
    ):
        with pytest.raises(ValueError):
            PlaceReview.model_validate(review("A", **bad))


def test_tokyo_review_file_is_valid_and_sourced() -> None:
    """저장소의 도쿄 검수 자료: 형식이 맞고, 공개되는 장소에는 출처가 있다."""
    path = Path(__file__).resolve().parents[1] / "place_reviews" / "tokyo.json"
    data = read_reviews(path)
    refs = [r.ref for r in data.reviews]
    assert len(refs) == len(set(refs))
    active = [r for r in data.reviews if r.status() == PlaceStatus.active]
    assert active and all(r.sources and r.name_ko and r.type for r in active)


@needs_db
def test_apply_reviews_fills_places_and_hours(conn: Connection) -> None:
    def cand(ref: str) -> PlaceCandidate:
        return PlaceCandidate(
            ref=ref, name_local=ref, name_ko=None, name_en=None, type="공원",
            popularity=3, lat=35.7, lon=139.7, website=None,
        )  # fmt: skip

    load_places(conn, SOURCE, "tokyo", [cand("open"), cand("gone"), cand("untouched")])
    data = ReviewFile.model_validate(
        {
            "city": "tokyo",
            "source": SOURCE,
            "checked": "2026-11-16",
            "method": "test",
            "reviews": [
                review("open", name_ko="열린 곳", type="박물관", indoor=True, cost_per_person=1000,
                       default_stay_min=90, sources=["https://example.test/"]),
                review("gone", verdict="closed_permanently", hours=None),
                review("not-in-db"),
            ],
        }
    )  # fmt: skip
    result = apply_reviews(conn, data)
    assert result.missing == ["not-in-db"] and result.hours_rows == 2
    assert result.by_status == {PlaceStatus.active: 1, PlaceStatus.closed: 1}

    rows = {p.source_ref: p for p in conn.execute(select(Place).where(Place.source == SOURCE)).all()}
    opened = rows["open"]
    assert (opened.status, opened.name_ko, opened.type, opened.indoor) == (
        PlaceStatus.active, "열린 곳", "박물관", True,
    )  # fmt: skip
    assert (opened.cost_per_person, opened.default_stay_min) == (1000, 90)
    assert opened.verified_at == datetime(2026, 11, 16, tzinfo=UTC)
    hours = conn.execute(
        select(PlaceHours.weekday, PlaceHours.open_time, PlaceHours.close_time, PlaceHours.last_entry_time)
        .where(PlaceHours.place_id == opened.id)
        .order_by(PlaceHours.weekday)
    ).all()
    assert [tuple(h) for h in hours] == [
        (1, time(9, 30), time(17, 0), time(16, 30)),  # 화
        (6, time(9, 30), time(17, 0), time(16, 30)),  # 일
    ]
    assert rows["gone"].status == PlaceStatus.closed and rows["gone"].type == "공원"  # 없는 값은 그대로
    assert rows["untouched"].status == PlaceStatus.hidden and rows["untouched"].verified_at is None

    # 다시 적용해도 같다(영업시간이 겹쳐 쌓이지 않는다)
    assert apply_reviews(conn, data).hours_rows == 2
    assert conn.execute(select(PlaceHours).where(PlaceHours.place_id == opened.id)).all().__len__() == 2


def test_read_reviews_reads_a_file(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(
        json.dumps(
            {"city": "tokyo", "source": "s", "checked": "2026-11-16", "method": "m", "reviews": [review("A")]}
        ),
        encoding="utf-8",
    )
    assert read_reviews(path).checked == date(2026, 11, 16)
