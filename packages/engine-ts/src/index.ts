// 기기에서 도는 결정론적 재계산 엔진 (PRD §5). generate·planTrip은 서버 전용.
export { autoAdjust, particle, type AdjustResult } from "./adjust";
export { compute } from "./compute";
export { approxLeg, routeKm } from "./route";
export { suggest, type SuggestMode, type SuggestOption } from "./suggest";
export * from "./types";
