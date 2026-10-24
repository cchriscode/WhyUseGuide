// 디자인 토큰 (PRD §9). 값은 PRD 표 그대로 두고, 바꿀 때는 PRD도 함께 고친다.

export const color = {
  primary: "#0F766E", // 주 버튼, 선택, 다음 이동 카드 배경
  primarySoft: "#E6F4F2", // 선택 배경, 안내 박스
  primaryInk: "#0B5C56", // 연한 배경 위 강조 글자
  ink: "#111827", // 본문
  sub: "#5B6270", // 보조 글자 (흰 배경 대비 4.5:1 이상)
  line: "#E7E8EC", // 구분선, 테두리
  surface: "#F4F5F7", // 분할 버튼 바탕, 보조 카드
  tipBg: "#FFF6E0", // 팁 박스
  tipInk: "#4A3300",
  dangerBg: "#FEF0EE", // 경고, 빼기
  danger: "#B42318",
  me: "#2563EB", // 지도 현재 위치
  white: "#FFFFFF",
} as const;

// 노선 색(line.*)은 토큰으로 두지 않고 GTFS route_color를 그대로 쓴다 (예: 긴자선 #F39700).

export const fontFamily = {
  base: "Pretendard",
  fallback: "Noto Sans KR",
} as const;

/** size / weight. 범위로 적힌 값(16~17 등)은 화면에서 고르도록 양 끝을 둔다. */
export const typography = {
  headline: { size: 28, weight: 800 },
  screenSentence: { size: 22, weight: 800 },
  cardTitle: { size: 16, sizeLarge: 17, weight: 700, weightBold: 800 },
  body: { size: 14, sizeLarge: 15, weight: 400, weightMedium: 500 },
  caption: { size: 12, sizeLarge: 13, weight: 400 },
  numeric: { fontVariant: ["tabular-nums"] },
} as const;

export const space = { 1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24 } as const;

/** 화면 좌우 여백 */
export const screenGutter = 20;

export const radius = {
  cardMin: 16,
  cardMax: 24,
  buttonMin: 14,
  buttonMax: 16,
  chip: 999,
} as const;

/** 바텀시트와 선택된 분할 버튼에만 쓴다. 0 1 3 rgba(17,24,39,0.14) */
export const shadow = {
  raised: { offsetX: 0, offsetY: 1, blur: 3, color: "rgba(17,24,39,0.14)" },
} as const;

export const size = {
  primaryButtonHeight: 56,
  minTouchTarget: 44,
  mapCardHeight: 210,
} as const;

export const tokens = {
  color,
  fontFamily,
  typography,
  space,
  screenGutter,
  radius,
  shadow,
  size,
} as const;

export default tokens;
