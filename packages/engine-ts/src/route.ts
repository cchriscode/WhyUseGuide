// 구간 경로 선택 (PRD §5.3). itinerary_engine.route와 같은 규칙·같은 반올림.
import { C, ceilMin, round2 } from "./config";
import type { City, Leg, LegMode, PlanMode, Transport } from "./types";

export interface Endpoint {
  id: string;
  lat: number;
  lon: number;
  /** 노선 id → 그 노선의 가장 가까운 역까지 직선 km. 빈 값 = 가까운 역 없음, undefined = 모름(숙소 등). */
  stations?: Record<string, number>;
}

export interface RouteLeg {
  mode: LegMode;
  minutes: number;
  fare: number;
  km: number;
  estimated: boolean;
  transfers: number;
  label: string;
}

const DEFAULT_TAXI = { base: 500, baseKm: 1.0, perKm: 400 };

export function straightKm(a: Endpoint, b: Endpoint): number {
  const rad = Math.PI / 180;
  const [lat1, lon1, lat2, lon2] = [a.lat * rad, a.lon * rad, b.lat * rad, b.lon * rad];
  const h = Math.sin((lat2 - lat1) / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin((lon2 - lon1) / 2) ** 2;
  return 6371.0 * 2 * Math.asin(Math.sqrt(h));
}

export function routeKm(a: Endpoint, b: Endpoint): number {
  return round2(straightKm(a, b) * C.approx.routeFactor);
}

function nearestKm(stations: Record<string, number> | undefined): number {
  if (stations === undefined) return C.approx.stationKmUnknown;
  const values = Object.values(stations);
  return values.length ? Math.min(...values) : C.approx.stationKmMax;
}

/** [환승 횟수, 양끝에서 탈 역까지 직선 km 합]. itinerary_engine.route.line_choice와 같은 규칙. */
export function lineChoice(a: Endpoint, b: Endpoint, city: City): [number, number] {
  const sa = a.stations ?? {};
  const sb = b.stations ?? {};
  const shared = Object.keys(sa).filter((x) => x in sb);
  if (shared.length) return [0, Math.min(...shared.map((x) => sa[x]! + sb[x]!))];
  const links = (city.lineLinks ?? []).map(([x, y]) => `${x}\u0000${y}`);
  const linked = (x: string, y: string) => links.includes(`${x}\u0000${y}`) || links.includes(`${y}\u0000${x}`);
  const pairs: number[] = [];
  for (const x of Object.keys(sa)) for (const y of Object.keys(sb)) if (linked(x, y)) pairs.push(sa[x]! + sb[y]!);
  if (pairs.length) return [1, Math.min(...pairs)];
  return [2, nearestKm(a.stations) + nearestKm(b.stations)];
}

export function transfersBetween(a: Endpoint, b: Endpoint, city: City): number {
  return lineChoice(a, b, city)[0];
}

export function transitFare(km: number, city: City): number {
  const bands = city.fareBands ?? [];
  for (const band of bands) if (km <= band.maxKm) return band.fare;
  return bands.length ? bands[bands.length - 1]!.fare : 0;
}

export function taxiFare(km: number, city: City): number {
  const t = city.taxi ?? DEFAULT_TAXI;
  return t.base + Math.max(0, Math.ceil((km - t.baseKm) * t.perKm - 1e-9));
}

function walk(km: number): RouteLeg {
  return { mode: "walk", minutes: ceilMin((km * 1000) / C.approx.walkMetersPerMin), fare: 0, km, estimated: true, transfers: 0, label: "" };
}

/** 역까지 걷기(직선 km × 1.3) + 기본 + km당 시간. */
function transit(km: number, [transfers, accessKm]: [number, number], city: City): RouteLeg {
  const [base, perKm] = [C.approx.direct, C.approx.transfer1, C.approx.transfer2][transfers]!;
  const accessWalk = (round2(accessKm * C.approx.routeFactor) * 1000) / C.approx.walkMetersPerMin;
  const minutes = ceilMin(accessWalk + base! + perKm! * km);
  return { mode: "transit", minutes, fare: transitFare(km, city), km, estimated: true, transfers, label: "" };
}

function taxi(km: number, city: City): RouteLeg {
  const [base, perKm] = C.approx.taxi;
  return { mode: "taxi", minutes: ceilMin(base! + perKm! * km), fare: taxiFare(km, city), km, estimated: true, transfers: 0, label: "" };
}

function car(km: number, city: City, parkingFee: number): RouteLeg {
  const c = C.approx.car;
  const minutes = c.base + c.cityPerKm * Math.min(km, c.cityKm) + c.highwayPerKm * Math.max(km - c.cityKm, 0);
  const fare = Math.floor(km * (city.fuelPerKm ?? 0) + 0.5) + parkingFee;
  return { mode: "car", minutes: ceilMin(minutes) + C.parkMin, fare, km, estimated: true, transfers: 0, label: "" };
}

export function approxLeg(
  a: Endpoint,
  b: Endpoint,
  planMode: PlanMode,
  transport: Transport,
  city: City,
  parkingFee = 0,
): RouteLeg {
  const km = routeKm(a, b);
  if (transport === "car") return car(km, city, parkingFee);
  const w = walk(km);
  const t = transit(km, lineChoice(a, b, city), city);
  if (planMode === "balance") return km <= C.walkOnlyKm ? w : t;
  if (planMode === "cheap") return km <= C.walkCheapKm ? w : t;
  // 빠르게: 대중교통 최단. 걷는 게 더 빠르면 걷고, 환승 구간이 길면 택시와 비교한다.
  let best = w.minutes <= t.minutes ? w : t;
  if (best === t && t.transfers >= 1 && km > C.taxiCompareKm) {
    const x = taxi(km, city);
    if (x.minutes < t.minutes) best = x;
  }
  return best;
}

export function legKey(from: string, to: string, planMode: PlanMode): string {
  return `${from}\u0000${to}\u0000${planMode}`;
}

export function chooseLeg(
  a: Endpoint,
  b: Endpoint,
  planMode: PlanMode,
  transport: Transport,
  city: City,
  known: Map<string, Leg>,
  parkingFee = 0,
): RouteLeg {
  const leg = known.get(legKey(a.id, b.id, planMode));
  if (leg) {
    return {
      mode: leg.mode,
      minutes: leg.minutes,
      fare: leg.fare,
      km: leg.km,
      estimated: leg.estimated ?? false,
      transfers: leg.transfers ?? 0,
      label: leg.label ?? "",
    };
  }
  return approxLeg(a, b, planMode, transport, city, parkingFee);
}
