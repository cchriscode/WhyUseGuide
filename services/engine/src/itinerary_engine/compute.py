"""일정 계산 compute(day) (PRD §5.1, §13.4 숙소·자동차)."""

from itinerary_engine.config import ceil_to, constants, to_hhmm, to_min
from itinerary_engine.models import END, START, Day, DayResult, Issue, LegRow, StopRow, Totals
from itinerary_engine.route import Endpoint, choose_leg


def endpoints(day: Day) -> list[Endpoint]:
    """장소 순서대로의 경로 끝점. 숙소가 있으면 앞뒤에 붙는다."""
    out = []
    if day.start_point:
        out.append(Endpoint(START, day.start_point.lat, day.start_point.lon))
    for item in day.items:
        p = day.places[item.place_id]
        out.append(Endpoint(p.id, p.lat, p.lon, p.stations))
    if day.end_point:
        out.append(Endpoint(END, day.end_point.lat, day.end_point.lon))
    return out


def compute(day: Day) -> DayResult:
    c = constants()
    known = {(leg.from_id, leg.to_id, leg.plan_mode): leg for leg in day.legs}
    points = endpoints(day)
    offset = 1 if day.start_point else 0  # points[offset + i] = items[i]

    rows: list[StopRow | LegRow] = []
    t = to_min(day.start)
    move = fare = spend = issues = 0
    driving = 0  # 자동차 연속 운전(분)
    end_of_day = t

    def add_leg(i_from: int, i_to: int, delay: int, parking_fee: int) -> int:
        nonlocal move, fare, driving
        a, b = points[i_from], points[i_to]
        leg = choose_leg(a, b, day.plan_mode, day.transport, day.city, known, parking_fee)
        brk = 0
        if day.transport == "car":
            if driving + leg.minutes > c["driveBreakEveryMin"]:
                brk = c["driveBreakMin"]
                driving = leg.minutes
            else:
                driving += leg.minutes
        rows.append(
            LegRow(
                from_id=a.id,
                to_id=b.id,
                mode=leg.mode,
                minutes=leg.minutes,
                fare=leg.fare,
                km=leg.km,
                estimated=leg.estimated,
                transfers=leg.transfers,
                label=leg.label,
                delay_min=delay,
                break_min=brk,
            )
        )
        move += leg.minutes
        fare += leg.fare
        return leg.minutes + delay + brk

    for i, item in enumerate(day.items):
        place = day.places[item.place_id]
        delay = day.delay.minutes if day.delay and day.delay.gap == i else 0
        if i > 0 or day.start_point:
            t += add_leg(offset + i - 1, offset + i, delay, place.parking_fee)
        arrive = ceil_to(t, c["slotMin"])
        stay = item.stay_min if item.stay_min is not None else place.default_stay_min
        issue: Issue | None = None
        if item.fixed_at:
            fixed = to_min(item.fixed_at)
            start = max(arrive, fixed)
            if arrive > fixed + c["fixedLateTolMin"]:
                issue = "late"
        else:
            start = arrive
            if place.open and arrive < to_min(place.open):
                start = to_min(place.open)
                if start - arrive > c["openWaitMaxMin"]:
                    issue = "open"
        if issue is None and (
            place.closed
            or (place.close and start + stay > to_min(place.close))
            or (place.last_entry and start > to_min(place.last_entry))
        ):
            issue = "close"
        end = start + stay
        rows.append(
            StopRow(
                place_id=place.id,
                name=place.name,
                arrive=to_hhmm(arrive),
                wait=start - arrive,
                start=to_hhmm(start),
                end=to_hhmm(end),
                stay_min=stay,
                issue=issue,
                fixed_at=item.fixed_at,
            )
        )
        issues += issue is not None
        spend += place.cost_per_person
        if stay >= 15:
            driving = 0
        t = end
        end_of_day = end

    if day.end_point and day.items:
        back = add_leg(len(points) - 2, len(points) - 1, 0, 0)
        if day.transport == "car":
            end_of_day = t + back  # 자동차 여행은 숙소 도착이 하루의 끝

    day_end = to_min(day.day_end or c["dayEnd"])
    return DayResult(
        rows=tuple(rows),
        totals=Totals(
            move_min=move,
            fare_per_person=fare,
            spend_per_person=fare + spend,
            end=to_hhmm(end_of_day),
            issues=issues,
            over_end=end_of_day > day_end,
        ),
    )
