// 디자인 토큰 (PRD §9, 캔버스 C1 줄 기준). 다크 모드는 2단계.
// 노선 색(line.*)은 토큰이 아니라 GTFS route_color 값을 그대로 쓴다.

export const colors = {
  primary: "#0F766E",
  primarySoft: "#E6F4F2",
  primaryInk: "#0B5C56",
  ink: "#111827",
  sub: "#5B6270",
  line: "#E7E8EC",
  surface: "#F4F5F7",
  white: "#FFFFFF",
  tipBg: "#FFF6E0",
  tipInk: "#4A3300",
  dangerBg: "#FEF0EE",
  danger: "#B42318",
  me: "#2563EB",
} as const;

// PRD는 크기·굵기를 범위로 정했다(카드 제목 16~17/700~800, 본문 14~15/400~500, 보조 12~13/400).
// 범위의 양 끝을 각각 토큰으로 둔다.
export const typography = {
  fontFamily: ["Pretendard", "Noto Sans KR", "sans-serif"],
  numeric: { fontVariant: ["tabular-nums"] },
  headline: { fontSize: 28, fontWeight: "800" },
  sentence: { fontSize: 22, fontWeight: "800" },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  cardTitleStrong: { fontSize: 17, fontWeight: "800" },
  body: { fontSize: 15, fontWeight: "400" },
  bodyStrong: { fontSize: 14, fontWeight: "500" },
  caption: { fontSize: 13, fontWeight: "400" },
  captionSmall: { fontSize: 12, fontWeight: "400" },
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24,
  screenX: 20,
} as const;

export const radius = {
  cardSm: 16,
  cardLg: 24,
  buttonSm: 14,
  button: 16,
  chip: 999,
} as const;

// 그림자는 바텀시트와 선택된 분할 버튼에만 쓴다.
export const shadow = {
  raised: { offsetX: 0, offsetY: 1, blur: 3, color: "rgba(17,24,39,0.14)" },
} as const;

export const size = {
  primaryButtonHeight: 56,
  minTouch: 44,
  routeMapHeight: 210,
} as const;

export const tokens = { colors, typography, spacing, radius, shadow, size } as const;
