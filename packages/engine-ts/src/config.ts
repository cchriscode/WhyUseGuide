import raw from "@whyuseguide/engine-config/constants.json";

// PRD §5.0 엔진 상수. Python 엔진과 같은 파일을 쓴다.
export const C = raw;

export function toMin(hhmm: string): number {
  const [h, m] = hhmm.split(":");
  return Number(h) * 60 + Number(m);
}

export function toHHMM(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** 소수 둘째 자리 0.5 올림 (Python 엔진의 round2와 같다). */
export function round2(x: number): number {
  return Math.floor(x * 100 + 0.5) / 100;
}

/** 분 올림. 부동소수점 오차로 정수 바로 위가 올림되지 않게 작은 여유를 둔다. 0은 -0이 아닌 0으로. */
export function ceilMin(x: number): number {
  return Math.ceil(x - 1e-9) + 0;
}

export function ceilTo(x: number, step: number): number {
  return Math.ceil(x / step) * step;
}
