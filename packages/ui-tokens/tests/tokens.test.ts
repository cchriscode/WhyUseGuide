import { colors, spacing } from "../src";

// WCAG 2.x 상대 휘도·대비 공식
function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)) as [
    number,
    number,
    number,
  ];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(fg: string, bg: string): number {
  const [hi, lo] = [luminance(fg), luminance(bg)].sort((a, b) => b - a) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

describe("글자·배경 대비 4.5:1 이상 (PRD §9, §10 접근성)", () => {
  const pairs: [string, string, string][] = [
    ["본문", colors.ink, colors.white],
    ["보조 글자", colors.sub, colors.white],
    ["주 버튼 글자", colors.white, colors.primary],
    ["연한 배경 위 강조", colors.primaryInk, colors.primarySoft],
    ["팁 박스", colors.tipInk, colors.tipBg],
    ["경고", colors.danger, colors.dangerBg],
    ["분할 버튼 바탕 위 보조 글자", colors.sub, colors.surface],
  ];

  it.each(pairs)("%s", (_name, fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("간격은 4의 배수 (PRD §9)", () => {
  it.each(Object.entries(spacing))("%s", (_name, value) => {
    expect(value % 4).toBe(0);
  });
});
