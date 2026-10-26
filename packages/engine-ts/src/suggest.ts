// 후보 점수: 대체 · 사이 들르기 · 남는 시간 (PRD §5.5). itinerary_engine.suggest와 같은 결과를 낸다.
import { C, toMin } from "./config";
import { compute } from "./compute";
import type { Day, DayResult, Place, StopRow } from "./types";

export type SuggestMode = "replace" | "between" | "fill";

export interface SuggestOption {
  placeId: string;
  name: string;
  position: number;
  score: number;
  detourMin: number;
  arrive: string;
  endAfter: string;
  absorbed: boolean;
  sameType: boolean;
  outsideHours: boolean;
  newIssues: number;
  badges: string[];
  effect: string;
  warning: string | null;
}

const round1 = (x: number): number => Math.floor(x * 10 + 0.5) / 10;
const stopsOf = (r: DayResult): StopRow[] => r.rows.filter((x): x is StopRow => x.kind === "stop");

function effect(detour: number, endDelta: number): string {
  const move = detour > 0 ? `${detour}분 돌아가요` : detour === 0 ? "이동 그대로" : `이동이 ${-detour}분 줄어요`;
  const end = endDelta <= 0 ? "끝나는 시간 그대로" : `끝나는 시간 ${endDelta}분 늦어져요`;
  return `${move} · ${end}`;
}

function evaluate(day: Day, base: DayResult, candidate: Place, position: number, removedType: string | null): SuggestOption {
  const w = C.suggestScore;
  const items = [...day.items];
  items.splice(position, 0, { placeId: candidate.id, stayMin: null, fixedAt: null });
  const trial: Day = { ...day, items, places: { ...day.places, [candidate.id]: candidate } };
  const after = compute(trial);
  const stops = stopsOf(after);
  const row = stops[position]!;

  const detour = after.totals.moveMin - base.totals.moveMin;
  const endDelta = toMin(after.totals.end) - toMin(base.totals.end);
  const othersWithIssue = stops.filter((s, i) => s.issue && i !== position).length;
  const newIssues = Math.max(0, othersWithIssue - base.totals.issues);
  const outside = row.issue !== null;
  const lateEnd = after.totals.overEnd;
  const same = removedType !== null && candidate.type === removedType;
  const absorbed = endDelta <= 0;
  const n = (b: boolean) => (b ? 1 : 0);

  // Python과 같은 순서로 더해야 부동소수점 결과가 같다.
  const score =
    w.move * detour +
    w.newIssue * newIssues +
    w.outsideHours * n(outside) +
    w.lateEnd * n(lateEnd) +
    w.rating * (candidate.rating ?? 0) +
    w.sameType * n(same) +
    w.absorbed * n(absorbed);

  const badges = (
    [
      ["기다리는 시간 활용", absorbed],
      ["비슷한 곳", same],
      ["가는 길", detour <= C.badges.onTheWayMaxDetourMin],
      ["실내", candidate.indoor ?? false],
    ] as const
  )
    .filter(([, on]) => on)
    .map(([label]) => label);

  let warning: string | null = null;
  if (outside) warning = "영업시간 밖이에요";
  else if (newIssues) warning = `다른 일정 ${newIssues}곳이 맞지 않게 돼요`;

  return {
    placeId: candidate.id,
    name: candidate.name,
    position,
    score: round1(score),
    detourMin: detour,
    arrive: row.arrive,
    endAfter: after.totals.end,
    absorbed,
    sameType: same,
    outsideHours: outside,
    newIssues,
    badges,
    effect: effect(detour, endDelta),
    warning,
  };
}

const byScoreThenId = (a: SuggestOption, b: SuggestOption) =>
  a.score - b.score || (a.placeId < b.placeId ? -1 : a.placeId > b.placeId ? 1 : 0);

export function suggest(
  day: Day,
  mode: SuggestMode,
  candidates: Place[],
  opts: { position?: number | null; cur?: number; limit?: number } = {},
): SuggestOption[] {
  const { position = null, cur = -1, limit = 3 } = opts;
  const inDay = new Set(day.items.map((it) => it.placeId));
  const pool = candidates.filter((p) => !inDay.has(p.id));
  const base = compute(day);
  let options: SuggestOption[] = [];
  if (mode === "replace") {
    if (position === null) throw new Error("replace needs position");
    const removed = day.places[day.items[position]!.placeId]!;
    const without: Day = { ...day, items: [...day.items.slice(0, position), ...day.items.slice(position + 1)] };
    options = pool.map((p) => evaluate(without, base, p, position, removed.type));
  } else if (mode === "between") {
    if (position === null) throw new Error("between needs position");
    options = pool.map((p) => evaluate(day, base, p, position, null));
  } else {
    for (const p of pool) {
      let best: SuggestOption | null = null;
      for (let pos = cur + 1; pos <= day.items.length; pos++) {
        const o = evaluate(day, base, p, pos, null);
        if (best === null || o.score < best.score || (o.score === best.score && o.position < best.position)) best = o;
      }
      if (best) options.push(best);
    }
  }
  options.sort(byScoreThenId);
  return options.slice(0, limit);
}
