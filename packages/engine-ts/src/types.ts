// 엔진 입출력 타입. services/engine(itinerary_engine.models)의 JSON 형태(camelCase)와 같다.

export type PlanMode = "fast" | "balance" | "cheap";
export type Transport = "transit" | "car";
export type LegMode = "walk" | "transit" | "taxi" | "car";
export type Issue = "late" | "open" | "close";

export const START = "@start";
export const END = "@end";

export interface Point {
  lat: number;
  lon: number;
}

export interface Place {
  id: string;
  name: string;
  type: string;
  lat: number;
  lon: number;
  defaultStayMin: number;
  rating?: number;
  costPerPerson?: number;
  open?: string | null;
  close?: string | null;
  lastEntry?: string | null;
  closed?: boolean;
  indoor?: boolean;
  parkingFee?: number;
  /** 노선 id → 그 노선의 가장 가까운 역까지 직선 km (근사 경로 §5.3) */
  stations?: Record<string, number>;
}

export interface Item {
  placeId: string;
  stayMin?: number | null;
  fixedAt?: string | null;
}

export interface Leg {
  from: string;
  to: string;
  planMode: PlanMode;
  mode: LegMode;
  minutes: number;
  fare: number;
  km: number;
  estimated?: boolean;
  label?: string;
  transfers?: number;
  /** 이 출발 준비 시각에 대해 구한 시간표 결과. 실제 출발 준비 시각이 다르면 쓰지 않고 근사한다. */
  departAt?: string | null;
  /** 열차 시각에 맞추려고 출발지에서 더 머무는 분. */
  waitMin?: number;
}

export interface TaxiFare {
  base: number;
  baseKm: number;
  perKm: number;
}

export interface City {
  lineLinks?: [string, string][];
  fareBands?: { maxKm: number; fare: number }[];
  /** 없으면 택시를 후보로 쓰지 않는다. */
  taxi?: TaxiFare | null;
  fuelPerKm?: number;
}

export interface Day {
  date: string;
  start: string;
  items: Item[];
  places: Record<string, Place>;
  city?: City;
  planMode?: PlanMode;
  transport?: Transport;
  legs?: Leg[];
  delay?: { gap: number; minutes: number } | null;
  startPoint?: Point | null;
  endPoint?: Point | null;
  dayEnd?: string | null;
}

export interface NormalizedItem {
  placeId: string;
  stayMin: number | null;
  fixedAt: string | null;
}

export interface StopRow {
  kind: "stop";
  placeId: string;
  name: string;
  arrive: string;
  wait: number;
  start: string;
  end: string;
  stayMin: number;
  issue: Issue | null;
  fixedAt: string | null;
}

export interface LegRow {
  kind: "leg";
  from: string;
  to: string;
  mode: LegMode;
  minutes: number;
  fare: number;
  km: number;
  estimated: boolean;
  transfers: number;
  label: string;
  delayMin: number;
  breakMin: number;
  waitMin: number;
  depart: string;
}

export interface Totals {
  moveMin: number;
  farePerPerson: number;
  spendPerPerson: number;
  end: string;
  issues: number;
  overEnd: boolean;
}

export interface DayResult {
  rows: (StopRow | LegRow)[];
  totals: Totals;
}
