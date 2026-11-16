// 공통 픽스처(/engine-fixtures)를 TS 엔진으로 계산해 Python 엔진 결과와 같은지 본다 (PRD P2 완료 기준).
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { autoAdjust, compute, suggest, type Day, type Place, type SuggestMode } from "../src";

const DIR = join(__dirname, "../../../engine-fixtures");

interface Fixture {
  name: string;
  kind: "compute" | "autoAdjust" | "suggest";
  input: { day: Day; cur?: number; mode?: SuggestMode; position?: number; candidates?: Place[] };
  expected: unknown;
}

const fixtures: Fixture[] = readdirSync(DIR)
  .filter((f) => f.endsWith(".json"))
  .sort()
  .map((f) => JSON.parse(readFileSync(join(DIR, f), "utf-8")) as Fixture);

function run(f: Fixture): unknown {
  const { day } = f.input;
  if (f.kind === "compute") return compute(day);
  if (f.kind === "autoAdjust") return autoAdjust(day, f.input.cur ?? -1);
  return suggest(day, f.input.mode!, f.input.candidates!, { position: f.input.position ?? null, cur: f.input.cur ?? -1 });
}

test("픽스처가 50개 이상", () => {
  expect(fixtures.length).toBeGreaterThanOrEqual(50);
});

test.each(fixtures.map((f) => [f.name, f] as const))("%s", (_name, f) => {
  expect(run(f)).toEqual(f.expected);
});
