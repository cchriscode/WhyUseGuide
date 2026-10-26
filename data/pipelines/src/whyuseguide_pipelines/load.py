from dataclasses import dataclass

from sqlalchemy import Connection, delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

from whyuseguide_api.db.models import City, Line, NameSource, Stop, StopLine
from whyuseguide_pipelines.gtfs import Network
from whyuseguide_pipelines.ko_names import line_name_ko, romaji_to_hangul


@dataclass
class LoadResult:
    lines: int
    stops: int
    stop_lines: int
    removed_lines: int
    removed_stops: int


def load_network(conn: Connection, source_id: str, city_code: str, network: Network) -> LoadResult:
    """출처 하나의 역·노선을 DB와 맞춘다: 있으면 갱신, 없으면 추가, 피드에서 빠진 것은 삭제.

    트랜잭션은 호출하는 쪽이 관리한다.
    """
    city_id = conn.execute(select(City.id).where(City.code == city_code)).scalar_one()

    line_rows = [
        {
            "city_id": city_id,
            "source_id": source_id,
            "gtfs_route_id": ln.route_id,
            "name_local": ln.name_local,
            "name_en": ln.name_en,
            "color_hex": ln.color_hex,
            "operator": ln.operator,
        }
        for ln in network.lines
    ]
    stmt = insert(Line).values(line_rows)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[Line.source_id, Line.gtfs_route_id],
            set_={k: stmt.excluded[k] for k in ("name_local", "name_en", "color_hex", "operator")}
            | {"updated_at": func.now()},
        )
    )

    stop_rows = [
        {
            "city_id": city_id,
            "source_id": source_id,
            "gtfs_stop_id": s.stop_id,
            "code": s.code,
            "name_local": s.name_local,
            "name_en": s.name_en,
            "location": func.ST_GeogFromText(f"SRID=4326;POINT({s.lon} {s.lat})"),
        }
        for s in network.stops
    ]
    stmt = insert(Stop).values(stop_rows)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[Stop.source_id, Stop.gtfs_stop_id],
            set_={k: stmt.excluded[k] for k in ("code", "name_local", "name_en", "location")}
            | {"updated_at": func.now()},
        )
    )

    removed_stops = conn.execute(
        delete(Stop).where(
            Stop.source_id == source_id, Stop.gtfs_stop_id.not_in([s.stop_id for s in network.stops])
        )
    ).rowcount
    removed_lines = conn.execute(
        delete(Line).where(
            Line.source_id == source_id, Line.gtfs_route_id.not_in([ln.route_id for ln in network.lines])
        )
    ).rowcount

    stop_ids = {
        k: v for k, v in conn.execute(select(Stop.gtfs_stop_id, Stop.id).where(Stop.source_id == source_id))
    }
    line_ids = {
        k: v for k, v in conn.execute(select(Line.gtfs_route_id, Line.id).where(Line.source_id == source_id))
    }

    for s in network.stops:
        parent = stop_ids.get(s.parent_stop_id) if s.parent_stop_id else None
        conn.execute(update(Stop).where(Stop.id == stop_ids[s.stop_id]).values(parent_station_id=parent))

    wanted = {(stop_ids[stop], line_ids[route]) for stop, route in network.stop_lines}
    conn.execute(
        delete(StopLine).where(
            StopLine.line_id.in_(list(line_ids.values())),
            tuple_(StopLine.stop_id, StopLine.line_id).not_in(wanted),
        )
    )
    if wanted:
        conn.execute(
            insert(StopLine)
            .values([{"stop_id": s, "line_id": ln} for s, ln in wanted])
            .on_conflict_do_nothing(index_elements=[StopLine.stop_id, StopLine.line_id])
        )

    return LoadResult(
        lines=len(line_rows),
        stops=len(stop_rows),
        stop_lines=len(wanted),
        removed_lines=removed_lines,
        removed_stops=removed_stops,
    )


@dataclass(frozen=True)
class KoreanNames:
    """names_ko.toml 편집 목록 (현지어 이름 → 한국어)."""

    lines: dict[str, str]
    stops: dict[str, str]


@dataclass
class NamingResult:
    editor: int = 0
    machine: int = 0
    official: int = 0
    missing: int = 0


def apply_korean_names(conn: Connection, source_id: str, names: KoreanNames) -> NamingResult:
    """name_ko를 채운다.

    공식 이름(official)은 그대로 두고, 편집 목록에 있으면 editor, 나머지는 기계 변환(machine)이다.
    """
    result = NamingResult()
    targets = (
        (Line, names.lines, line_name_ko),
        (Stop, names.stops, romaji_to_hangul),
    )
    for model, edited, convert in targets:
        rows = conn.execute(
            select(model.id, model.name_local, model.name_en, model.name_ko_source).where(
                model.source_id == source_id
            )
        )
        for row_id, name_local, name_en, current in rows.all():
            if current == NameSource.official:
                result.official += 1
                continue
            name_ko: str | None
            source: NameSource | None
            if name_local in edited:
                name_ko, source = edited[name_local], NameSource.editor
                result.editor += 1
            else:
                name_ko = convert(name_en) if name_en else None
                source = NameSource.machine if name_ko else None
                if name_ko:
                    result.machine += 1
                else:
                    result.missing += 1
            conn.execute(
                update(model).where(model.id == row_id).values(name_ko=name_ko, name_ko_source=source)
            )
    return result
