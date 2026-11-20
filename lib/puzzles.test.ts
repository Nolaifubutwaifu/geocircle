import { describe, expect, it } from "vitest";
import { pickNext, type Puzzle } from "./puzzles";

const p = (id: number | string) => ({ id }) as Puzzle;
const pool = [p(1), p(2), p(3)];

describe("pickNext", () => {
  it("never returns the current puzzle", () => {
    for (const r of [0, 0.5, 0.99]) expect(pickNext(pool, 2, new Set(), () => r)!.id).not.toBe(2);
  });
  it("only returns unplayed puzzles", () => {
    for (const r of [0, 0.5, 0.99]) expect(pickNext(pool, 1, new Set(["2"]), () => r)!.id).toBe(3);
  });
  it("returns null once everything is played", () => {
    expect(pickNext(pool, 1, new Set(["2", "3"]))).toBeNull();
    expect(pickNext([p(1)], 1, new Set())).toBeNull();
  });
});
