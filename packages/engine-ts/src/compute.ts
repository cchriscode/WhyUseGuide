// 일정 계산 compute(day) (PRD §5.1, §13.4). itinerary_engine.compute와 같은 결과를 낸다.
import { C, ceilTo, toHHMM, toMin } from "./config";
import { chooseLeg, legKey, type Endpoint } from "./route";
import type { Day, DayResult, Issue, Leg, LegRow, StopRow } from "./types";
import { END, START } from "./types";

export function stayOf(day: Day, item: { placeId: string; stayMin?: number | null }): number {
  return item.stayMin ?? day.places[item.placeId]!.defaultStayMin;
}

export function endpoints(day: Day): Endpoint[] {
  const out: Endpoint[] = [];
  if (day.startPoint) out.push({ id: START, lat: day.startPoint.lat, lon: day.startPoint.lon });
  for (const item of day.items) {
    const p = day.places[item.placeId]!;
    out.push({ id: p.id, lat: p.lat, lon: p.lon, stations: p.stations ?? {} });
  }
  if (day.endPoint) out.push({ id: END, lat: day.endPoint.lat, lon: day.endPoint.lon });
  return out;
}

export function compute(day: Day): DayResult {
  const known = new Map<string, Leg>((day.legs ?? []).map((l) => [legKey(l.from, l.to, l.planMode), l]));
  const city = day.city ?? {};
  const planMode = day.planMode ?? "balance";
  const transport = day.transport ?? "transit";
  const points = endpoints(day);
  const offset = day.startPoint ? 1 : 0;

  const rows: (StopRow | LegRow)[] = [];
  let t = toMin(day.start);
  let move = 0;
  let fare = 0;
  let spend = 0;
  let issues = 0;
  let driving = 0;
  let endOfDay = t;

  const addLeg = (iFrom: number, iTo: number, delay: number, parkingFee: number): number => {
    const a = points[iFrom]!;
    const b = points[iTo]!;
    const leg = chooseLeg(a, b, planMode, transport, city, known, parkingFee);
    let brk = 0;
    if (transport === "car") {
      if (driving + leg.minutes > C.driveBreakEveryMin) {
        brk = C.driveBreakMin;
        driving = leg.minutes;
      } else {
        driving += leg.minutes;
      }
    }
    rows.push({
      kind: "leg",
      from: a.id,
      to: b.id,
      mode: leg.mode,
      minutes: leg.minutes,
      fare: leg.fare,
      km: leg.km,
      estimated: leg.estimated,
      transfers: leg.transfers,
      label: leg.label,
      delayMin: delay,
      breakMin: brk,
    });
    move += leg.minutes;
    fare += leg.fare;
    return leg.minutes + delay + brk;
  };

  day.items.forEach((item, i) => {
    const place = day.places[item.placeId]!;
    const delay = day.delay && day.delay.gap === i ? day.delay.minutes : 0;
    if (i > 0 || day.startPoint) t += addLeg(offset + i - 1, offset + i, delay, place.parkingFee ?? 0);
    const arrive = ceilTo(t, C.slotMin);
    const stay = stayOf(day, item);
    let issue: Issue | null = null;
    let start: number;
    if (item.fixedAt) {
      const fixed = toMin(item.fixedAt);
      start = Math.max(arrive, fixed);
      if (arrive > fixed + C.fixedLateTolMin) issue = "late";
    } else {
      start = arrive;
      if (place.open && arrive < toMin(place.open)) {
        start = toMin(place.open);
        if (start - arrive > C.openWaitMaxMin) issue = "open";
      }
    }
    if (
      issue === null &&
      (place.closed ||
        (place.close && start + stay > toMin(place.close)) ||
        (place.lastEntry && start > toMin(place.lastEntry)))
    ) {
      issue = "close";
    }
    const end = start + stay;
    rows.push({
      kind: "stop",
      placeId: place.id,
      name: place.name,
      arrive: toHHMM(arrive),
      wait: start - arrive,
      start: toHHMM(start),
      end: toHHMM(end),
      stayMin: stay,
      issue,
      fixedAt: item.fixedAt ?? null,
    });
    if (issue !== null) issues += 1;
    spend += place.costPerPerson ?? 0;
    if (stay >= 15) driving = 0;
    t = end;
    endOfDay = end;
  });

  if (day.endPoint && day.items.length) {
    const back = addLeg(points.length - 2, points.length - 1, 0, 0);
    if (transport === "car") endOfDay = t + back;
  }

  const dayEnd = toMin(day.dayEnd ?? C.dayEnd);
  return {
    rows,
    totals: {
      moveMin: move,
      farePerPerson: fare,
      spendPerPerson: fare + spend,
      end: toHHMM(endOfDay),
      issues,
      overEnd: endOfDay > dayEnd,
    },
  };
}
