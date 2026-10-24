import { color, radius, space } from "./index";

// WCAG 2.x 상대 휘도 대비
function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  const channel = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return (
    0.2126 * channel((n >> 16) & 0xff) +
    0.7152 * channel((n >> 8) & 0xff) +
    0.0722 * channel(n & 0xff)
  );
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

describe("color tokens (PRD §9)", () => {
  it("모든 색이 #RRGGBB 형식이다", () => {
    for (const v of Object.values(color)) expect(v).toMatch(/^#[0-9A-F]{6}$/);
  });

  it.each([
    ["sub on white", color.sub, color.white],
    ["ink on white", color.ink, color.white],
    ["white on primary", color.white, color.primary],
    ["primaryInk on primarySoft", color.primaryInk, color.primarySoft],
    ["tipInk on tipBg", color.tipInk, color.tipBg],
    ["danger on dangerBg", color.danger, color.dangerBg],
  ])("%s 대비 4.5:1 이상 (PRD §10 접근성)", (_name, fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("spacing·shape", () => {
  it("간격은 4의 배수", () => {
    for (const v of Object.values(space)) expect(v % 4).toBe(0);
  });

  it("칩은 완전 둥근 모서리", () => {
    expect(radius.chip).toBe(999);
  });
});
