import { describe, expect, it } from "vitest";
import { playerStats, untilMidnight } from "./stats";
import { decodeShape, encodeShape, readChallenge } from "./share";
import type { Result } from "./storage";
import type { Shape } from "./geo";

const at = (d: number, h = 12) => new Date(2026, 10, d, h); // November 2026, local time
const res = (score: number, day: number): Result => ({
  score,
  pct: 1,
  shape: { kind: "circle", center: [0, 0], radiusKm: 100 },
  playedAt: at(day).toISOString(),
});
const sched = { "2026-11-18": 1, "2026-11-19": 2, "2026-11-20": 3, "2026-11-21": 4, "2026-11-22": 5 };

describe("playerStats", () => {
  it("counts consecutive days played on the day, and keeps the streak until today ends", () => {
    const r = { 1: res(40, 18), 2: res(60, 19), 3: res(80, 20) };
    expect(playerStats(r, sched, at(20))).toEqual({ played: 3, avg: 60, streak: 3, best: 3 });
    expect(playerStats(r, sched, at(21)).streak).toBe(3); // today not played yet
    expect(playerStats(r, sched, at(22)).streak).toBe(0); // missed yesterday
  });
  it("doesn't count a daily played on another day, but counts it as played", () => {
    const r = { 1: res(50, 18), 2: res(50, 20), 3: res(50, 20) }; // No. 2 played a day late
    expect(playerStats(r, sched, at(20))).toEqual({ played: 3, avg: 50, streak: 1, best: 1 });
  });
  it("ignores country puzzles in the counts", () => {
    expect(playerStats({ "c-aus": res(90, 20) }, sched, at(20)).played).toBe(0);
  });
});

describe("untilMidnight", () => {
  it("formats hours and minutes", () => {
    expect(untilMidnight(new Date(2026, 10, 20, 18, 48))).toBe("5h 12m");
    expect(untilMidnight(new Date(2026, 10, 20, 23, 30))).toBe("30m");
  });
});

describe("challenge links", () => {
  it("round-trips shapes", () => {
    expect(decodeShape(encodeShape({ kind: "circle", center: [151.2093, -33.8688], radiusKm: 420.4 }))).toEqual({
      kind: "circle",
      center: [151.21, -33.87],
      radiusKm: 420,
    });
    const t: Shape = { kind: "triangle", pins: [[1, 2], [3, 4], [5, 6]] };
    expect(decodeShape(encodeShape(t))).toEqual(t);
  });
  it("rejects junk", () => {
    for (const s of [null, "", "c,1,2", "c,1,2,3", "c,1,99,500", "x,1,2,3", "c,a,b,c", "t,1,2,3,4,5"]) expect(decodeShape(s)).toBeNull();
    expect(readChallenge("?beat=101")).toBeNull();
    expect(readChallenge("?beat=abc")).toBeNull();
    expect(readChallenge("?beat=64&s=junk")).toEqual({ score: 64, shape: null });
  });
});
