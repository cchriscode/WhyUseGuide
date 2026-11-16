"""코스 평가 → 다음 날 재생성 가중치 (PRD §5.6, MVP 규칙)."""

from typing import Literal

from itinerary_engine.config import constants
from itinerary_engine.generate import Prefs, Profile
from itinerary_engine.models import EngineModel

# S-09 태그 문구
TAG_LONG_MOVES = "이동이 길었어요"
TAG_TOO_TIGHT = "일정이 빡빡했어요"
TAG_RELAXED = "여유로웠어요"
TAG_LOCAL_GOOD = "로컬 스팟 좋았어요"
TAG_CROWDED = "사람이 너무 많았어요"
TAG_LONG_QUEUE = "대기 줄이 길었어요"


class CourseRating(EngineModel):
    stars: int  # 1~5
    tags: tuple[str, ...] = ()
    votes: dict[str, Literal[1, -1]] = {}  # 장소별 좋아요(+1)/별로(−1)
    route_accuracy: Literal["ok", "bit", "off"] | None = None


class FeedbackResult(EngineModel):
    prefs: Prefs
    messages: tuple[str, ...]  # "다음 추천에 이렇게 반영돼요" 미리보기
    verify_routes: bool  # 정확도 "달랐어요" → 그날 구간을 검증 큐에 등록


def apply_rating(
    prefs: Prefs, rating: CourseRating, profiles: dict[str, Profile], visited_types: list[str]
) -> FeedbackResult:
    """visited_types: 그날 다녀온 장소 유형(별점이 낮을 때 다음 날 줄일 유형).

    수치는 constants.json의 feedback(PRD §5.6). 미리보기 문구의 숫자도 거기서 만든다.
    """
    f = constants()["feedback"]
    p = prefs.model_dump()
    tags = dict(prefs.tag_weights)
    messages: list[str] = []

    if TAG_LONG_MOVES in rating.tags:
        p["move_cap_factor"] = round(prefs.move_cap_factor * f["longMovesCapFactor"], 4)
        messages.append(f"하루 이동 시간을 {round((1 - f['longMovesCapFactor']) * 100)}% 줄여요")
    if TAG_TOO_TIGHT in rating.tags:
        p["place_count_delta"] += f["tightPlaceCountDelta"]
        p["slack_min"] += f["tightSlackMin"]
        fewer, slack = -f["tightPlaceCountDelta"], f["tightSlackMin"]
        messages.append(f"하루 장소를 {fewer}곳 줄이고 장소 사이에 {slack}분 여유를 둬요")
    if TAG_RELAXED in rating.tags:
        p["place_count_delta"] += f["relaxedPlaceCountDelta"]
        messages.append(f"하루에 {f['relaxedPlaceCountDelta']}곳 더 넣을 수 있어요")
    if TAG_LOCAL_GOOD in rating.tags:
        p["mix_shift"] += f["localMixShift"]
        messages.append("로컬 장소를 더 넣어요")
    if TAG_CROWDED in rating.tags:
        p["mix_shift"] += f["crowdedMixShift"]
        p["crowd_avoid"] = True
        messages.append("유명한 곳은 덜 붐비는 오전에 가요")
    if TAG_LONG_QUEUE in rating.tags:
        p["booking_first"] = True
        messages.append("예약할 수 있는 곳은 예약 버튼을 먼저 보여줘요")

    for place_id, vote in sorted(rating.votes.items()):
        for tag in sorted(profiles.get(place_id, Profile()).tags):
            tags[tag] = round(tags.get(tag, 0.0) + f["voteTagWeight"] * vote, 4)
    if rating.votes:
        liked = sum(1 for v in rating.votes.values() if v > 0)
        disliked = len(rating.votes) - liked
        if liked:
            messages.append(f"좋았던 {liked}곳과 비슷한 장소를 더 넣어요")
        if disliked:
            messages.append(f"별로였던 {disliked}곳과 비슷한 장소는 줄여요")
    p["tag_weights"] = tags

    if rating.stars <= f["differentTypesMaxStars"] and visited_types:
        p["avoid_types"] = tuple(sorted(set(prefs.avoid_types) | set(visited_types)))
        messages.append("내일은 오늘과 다른 유형의 장소를 더 넣어요")

    verify = rating.route_accuracy == "off"
    if verify:
        messages.append("오늘 다닌 구간의 노선·시간을 다시 확인할게요")
    return FeedbackResult(prefs=Prefs.model_validate(p), messages=tuple(messages), verify_routes=verify)
