import { describe, expect, it } from "vitest";
import { pickRandom, type Puzzle } from "./puzzles";

const p = (id: number | string) => ({ id }) as Puzzle;
const pool = [p(1), p(2), p(3)];

describe("pickRandom", () => {
  it("never returns the current puzzle", () => {
    for (const r of [0, 0.5, 0.99]) expect(pickRandom(pool, 2, new Set(), () => r)!.id).not.toBe(2);
  });
  it("prefers unplayed puzzles", () => {
    for (const r of [0, 0.5, 0.99]) expect(pickRandom(pool, 1, new Set(["2"]), () => r)!.id).toBe(3);
  });
  it("falls back to played ones when everything is played", () => {
    expect(pickRandom(pool, 1, new Set(["1", "2", "3"]), () => 0)!.id).toBe(2);
  });
  it("returns null when there is nothing else", () => {
    expect(pickRandom([p(1)], 1, new Set())).toBeNull();
  });
});
