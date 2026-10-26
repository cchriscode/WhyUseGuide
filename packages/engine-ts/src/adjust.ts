// 자동 조정 autoAdjust(day, cur) (PRD §5.4). itinerary_engine.adjust와 같은 결과를 낸다.
// 순열 순서(사전순), 동점 처리, 메시지 문구까지 Python과 맞춘다.
import { C } from "./config";
import { compute, stayOf } from "./compute";
import type { Day, DayResult, NormalizedItem, StopRow } from "./types";

export interface AdjustResult {
  items: NormalizedItem[];
  result: DayResult;
  applied: boolean;
  messages: string[];
  dropSuggestion: { placeId: string; name: string } | null;
  fillGap: { position: number; waitMin: number } | null;
  undoItems: NormalizedItem[];
}

/** 받침이 있으면 withFinal(을·이), 없으면 withoutFinal(를·가). 한글로 끝나지 않으면 둘 다. */
export function particle(word: string, withFinal: string, withoutFinal: string): string {
  const last = word.length ? word[word.length - 1]! : "";
  if (last >= "가" && last <= "힣") {
    return (last.charCodeAt(0) - 0xac00) % 28 ? withFinal : withoutFinal;
  }
  return `${withFinal}(${withoutFinal})`;
}

const norm = (it: { placeId: string; stayMin?: number | null; fixedAt?: string | null }): NormalizedItem => ({
  placeId: it.placeId,
  stayMin: it.stayMin ?? null,
  fixedAt: it.fixedAt ?? null,
});

const stopsOf = (r: DayResult): StopRow[] => r.rows.filter((x): x is StopRow => x.kind === "stop");

type Key = [number, number, number];
const keyOf = (r: DayResult): Key => [r.totals.issues, r.totals.overEnd ? 1 : 0, r.totals.moveMin];
const less = (a: Key, b: Key): boolean => {
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i]! < b[i]!;
  return false;
};
const ok = (r: DayResult): boolean => r.totals.issues === 0 && !r.totals.overEnd;
const withItems = (day: Day, items: NormalizedItem[]): Day => ({ ...day, items });

/** itertools.permutations(range(n))와 같은 사전순. */
export function permutations(n: number): number[][] {
  const out: number[][] = [];
  const used = new Array<boolean>(n).fill(false);
  const cur: number[] = [];
  const rec = () => {
    if (cur.length === n) {
      out.push([...cur]);
      return;
    }
    for (let i = 0; i < n; i++) {
      if (used[i]) continue;
      used[i] = true;
      cur.push(i);
      rec();
      cur.pop();
      used[i] = false;
    }
  };
  rec();
  return out;
}

function reorder(day: Day, cur: number): Day {
  const items = day.items.map(norm);
  const head = items.slice(0, cur + 1);
  const tail = items.slice(cur + 1);
  if (tail.length < 2) return day;
  let bestDay = day;
  let best = keyOf(compute(day));
  if (tail.length <= C.permuteMax) {
    for (const order of permutations(tail.length)) {
      const cand = withItems(day, [...head, ...order.map((i) => tail[i]!)]);
      const k = keyOf(compute(cand));
      if (less(k, best)) {
        bestDay = cand;
        best = k;
      }
    }
    return bestDay;
  }
  let improved = true;
  while (improved) {
    improved = false;
    const current = bestDay.items.slice(cur + 1).map(norm);
    outer: for (let i = 0; i < current.length - 1; i++) {
      for (let j = i + 1; j < current.length; j++) {
        const swapped = [...current.slice(0, i), ...current.slice(i, j + 1).reverse(), ...current.slice(j + 1)];
        const cand = withItems(day, [...head, ...swapped]);
        const k = keyOf(compute(cand));
        if (less(k, best)) {
          bestDay = cand;
          best = k;
          improved = true;
          break outer;
        }
      }
    }
  }
  return bestDay;
}

function trim(day: Day, cur: number, original: Map<string, number>): Day {
  const floor = new Map<string, number>();
  for (const [pid, stay] of original) floor.set(pid, Math.max(C.trimFloor.minMin, Math.ceil(stay * C.trimFloor.ratio)));
  for (;;) {
    const result = compute(day);
    const stops = stopsOf(result);
    const problem = stops.findIndex((s, i) => i > cur && (s.issue === "late" || s.issue === "close"));
    let from: number;
    let to: number;
    if (problem >= 0) {
      const last = stops[problem]!.issue === "close" ? problem : problem - 1;
      from = cur + 1;
      to = last;
    } else if (result.totals.overEnd) {
      from = cur + 1;
      to = stops.length - 1;
    } else {
      return day;
    }
    const candidates: number[] = [];
    for (let i = from; i <= to; i++) {
      const it = day.items[i]!;
      if (!it.fixedAt && stayOf(day, it) > floor.get(it.placeId)!) candidates.push(i);
    }
    if (!candidates.length) return day;
    // 가장 긴 체류, 같으면 앞쪽
    let target = candidates[0]!;
    for (const i of candidates) {
      if (stayOf(day, day.items[i]!) > stayOf(day, day.items[target]!)) target = i;
    }
    const item = day.items[target]!;
    const newStay = Math.max(floor.get(item.placeId)!, stayOf(day, item) - C.trimStepMin);
    const items = day.items.map(norm);
    items[target] = { ...norm(item), stayMin: newStay };
    day = withItems(day, items);
  }
}

function delayMessage(day: Day): string | null {
  if (!day.delay || day.delay.minutes <= 0) return null;
  const without = compute({ ...day, delay: null });
  const withDelay = compute(day);
  if (without.totals.end === withDelay.totals.end) return `${day.delay.minutes}분 지연은 예약 전 대기 시간으로 흡수돼요`;
  return `뒤 일정이 ${day.delay.minutes}분씩 밀렸어요`;
}

export function autoAdjust(day: Day, cur = -1): AdjustResult {
  const originalItems = day.items.map(norm);
  const originalStay = new Map(day.items.map((it) => [it.placeId, stayOf(day, it)] as const));
  const messages: string[] = [];
  const dm = delayMessage(day);
  if (dm) messages.push(dm);

  const before = compute(day);
  if (ok(before)) {
    return { items: originalItems, result: before, applied: false, messages, dropSuggestion: null, fillGap: null, undoItems: originalItems };
  }

  const adjusted = trim(reorder(day, cur), cur, originalStay);
  const adjustedItems = adjusted.items.map(norm);
  const result = compute(adjusted);

  const oldPos = new Map(originalItems.map((it, i) => [it.placeId, i] as const));
  const moved: [string, number][] = [];
  adjustedItems.forEach((it, i) => {
    if (oldPos.get(it.placeId) !== i) moved.push([day.places[it.placeId]!.name, i + 1]);
  });
  if (moved.length) {
    messages.push("순서를 바꿨어요: " + moved.map(([name, pos]) => `${name}${particle(name, "을", "를")} ${pos}번째로`).join(", "));
  }
  for (const it of adjustedItems) {
    const stay = stayOf(adjusted, it);
    if (stay !== originalStay.get(it.placeId)) {
      messages.push(`${day.places[it.placeId]!.name} 체류 ${originalStay.get(it.placeId)}분 → ${stay}분`);
    }
  }

  let drop: AdjustResult["dropSuggestion"] = null;
  let fill: AdjustResult["fillGap"] = null;
  const stops = stopsOf(result);
  const remaining = stops.filter((s) => s.issue);
  if (remaining.length && remaining.every((s) => s.issue === "open") && !result.totals.overEnd) {
    const first = stops.findIndex((s) => s.issue === "open");
    fill = { position: first, waitMin: stops[first]!.wait };
    messages.push(`${stops[first]!.name} 오픈까지 ${stops[first]!.wait}분이 비어요`);
  } else if (remaining.length || result.totals.overEnd) {
    // (평점, 인덱스, id)가 가장 작은 곳
    let bestKey: [number, number, string] | null = null;
    adjustedItems.forEach((it, i) => {
      if (i <= cur || it.fixedAt) return;
      const k: [number, number, string] = [day.places[it.placeId]!.rating ?? 0, i, it.placeId];
      if (
        bestKey === null ||
        k[0] < bestKey[0] ||
        (k[0] === bestKey[0] && (k[1] < bestKey[1] || (k[1] === bestKey[1] && k[2] < bestKey[2])))
      ) {
        bestKey = k;
      }
    });
    if (bestKey !== null) {
      const pid = (bestKey as [number, number, string])[2];
      drop = { placeId: pid, name: day.places[pid]!.name };
    }
  }

  const end = result.totals.end;
  if (ok(result)) {
    messages.push(`조정 후 ${end} 종료 · 예약과 영업시간 모두 맞아요`);
  } else if (fill === null) {
    const problems = result.totals.issues + (result.totals.overEnd ? 1 : 0);
    const over = result.totals.overEnd ? " · 종료 목표를 넘어요" : "";
    const tail = drop ? ` · ${drop.name} 빼기를 제안해요` : "";
    messages.push(`조정 후 ${end} 종료 · 아직 맞지 않는 곳 ${problems}건${over}${tail}`);
  }

  const applied = JSON.stringify(adjustedItems) !== JSON.stringify(originalItems);
  return { items: adjustedItems, result, applied, messages, dropSuggestion: drop, fillGap: fill, undoItems: originalItems };
}
