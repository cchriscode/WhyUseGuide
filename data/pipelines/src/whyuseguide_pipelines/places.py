"""장소 후보 적재 (PRD §6 places). 검수 전 후보는 status = hidden, verified_at = NULL로 넣는다.

편집자가 검수한(verified_at이 있는) 장소는 출처 데이터를 다시 받아도 덮어쓰지 않는다.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, func, select, text
from sqlalchemy.dialects.postgresql import insert

from itinerary_engine.config import constants
from whyuseguide_api.db.models import City, Place, PlaceStatus

# Wikidata 분류(Q-ID) → PRD 장소 유형. 한 장소에 여럿이면 앞쪽이 이긴다.
WIKIDATA_TYPES: list[tuple[set[str], str]] = [
    ({"Q33506", "Q207694"}, "박물관"),
    ({"Q845945", "Q5393308"}, "신사·사찰"),
    ({"Q12518", "Q1440300"}, "전망"),
    ({"Q22698"}, "공원"),  # 공원이면서 정원인 곳(우에노 공원 등)은 공원으로
    ({"Q1107656"}, "정원"),
    ({"Q37654"}, "시장"),
    ({"Q570116"}, "체험"),  # 일반 관광 명소: 검수 때 유형을 다시 정한다
]
_unknown = {t for _, t in WIKIDATA_TYPES} - set(constants()["placeTypes"])
if _unknown:
    raise ValueError(
        f"WIKIDATA_TYPES에 정의되지 않은 장소 유형이 있다(constants.json placeTypes): {sorted(_unknown)}"
    )

DEFAULT_STAY = {"박물관": 90, "신사·사찰": 45}
INDOOR_TYPES = {"박물관"}


@dataclass(frozen=True)
class PlaceCandidate:
    ref: str  # Q-ID
    name_local: str
    name_ko: str | None
    name_en: str | None
    type: str
    lat: float
    lon: float
    popularity: int
    website: str | None


def popularity_from_sitelinks(links: int) -> int:
    """위키 문서 수(여러 언어판)로 유명도 1~5를 매긴다."""
    for threshold, level in ((50, 5), (20, 4), (10, 3), (5, 2)):
        if links >= threshold:
            return level
    return 1


@dataclass
class _Item:
    links: int
    loc: str
    classes: set[str] = field(default_factory=set)
    labels: dict[str, str] = field(default_factory=dict)
    site: str | None = None


def parse_wikidata(raw: dict[str, Any]) -> list[PlaceCandidate]:
    """SPARQL 결과(한 장소가 분류·사이트마다 여러 행) → 장소당 하나. 유형을 정할 수 없는 장소는 뺀다."""
    items: dict[str, _Item] = {}
    for b in raw["results"]["bindings"]:
        ref = b["item"]["value"].rsplit("/", 1)[-1]
        it = items.setdefault(ref, _Item(links=int(b["links"]["value"]), loc=b["loc"]["value"]))
        it.classes.add(b["cls"]["value"].rsplit("/", 1)[-1])
        for lang in ("ko", "ja", "en"):
            if lang in b:
                it.labels.setdefault(lang, b[lang]["value"])
        if "site" in b and it.site is None:
            it.site = b["site"]["value"]
    out = []
    for ref in sorted(items):
        it = items[ref]
        place_type = next((t for ids, t in WIKIDATA_TYPES if ids & it.classes), None)
        if place_type is None:
            continue
        lon, lat = it.loc.removeprefix("Point(").removesuffix(")").split()
        out.append(
            PlaceCandidate(
                ref=ref,
                name_local=it.labels.get("ja") or it.labels.get("en") or ref,
                name_ko=it.labels.get("ko"),
                name_en=it.labels.get("en"),
                type=place_type,
                lat=float(lat),
                lon=float(lon),
                popularity=popularity_from_sitelinks(it.links),
                website=it.site,
            )
        )
    return out


def load_wikidata_file(path: Path) -> list[PlaceCandidate]:
    return parse_wikidata(json.loads(path.read_text(encoding="utf-8")))


def load_places(
    conn: Connection, source: str, city_code: str, candidates: list[PlaceCandidate]
) -> tuple[int, int]:
    """후보를 넣거나(새 장소) 갱신한다(검수 전 장소만). 반환: (넣거나 갱신한 수, 검수돼서 건너뛴 수)."""
    city = conn.execute(select(City.id, City.currency).where(City.code == city_code)).one()
    rows = [
        {
            "city_id": city.id,
            "source": source,
            "source_ref": c.ref,
            "name_local": c.name_local,
            "name_ko": c.name_ko,
            "name_en": c.name_en,
            "type": c.type,
            "indoor": c.type in INDOOR_TYPES,
            "popularity": c.popularity,
            "default_stay_min": DEFAULT_STAY.get(c.type, 60),
            "currency": city.currency,
            "location": func.ST_GeogFromText(f"SRID=4326;POINT({c.lon} {c.lat})"),
            "website": c.website,
            "status": PlaceStatus.hidden,
        }
        for c in candidates
    ]
    if not rows:
        return (0, 0)
    stmt = insert(Place).values(rows)
    updatable = ("name_local", "name_ko", "name_en", "type", "indoor", "popularity", "location", "website")
    # 검수된 장소는 WHERE에 걸려 RETURNING에 나오지 않는다.
    written = len(
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[Place.source, Place.source_ref],
                set_={k: stmt.excluded[k] for k in updatable} | {"updated_at": func.now()},
                where=Place.verified_at.is_(None),
            ).returning(Place.id)
        ).all()
    )
    # 가장 가까운 역(도시 안). 검수된 장소는 편집자가 정한 값을 둔다.
    conn.execute(
        text(
            """
            UPDATE places p SET nearest_stop_id = (
              SELECT s.id FROM stops s WHERE s.city_id = p.city_id
              ORDER BY s.location <-> p.location LIMIT 1
            )
            WHERE p.source = :source AND p.verified_at IS NULL
            """
        ),
        {"source": source},
    )
    return (written, len(rows) - written)
