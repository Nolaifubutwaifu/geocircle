import { describe, expect, it } from "vitest";
import schedule from "@/public/data/schedule.json";
import { archive, localDate, released, todaysPuzzle, type Puzzle } from "./puzzles";

const p = (id: number) => ({ id }) as Puzzle;
const pool = [p(1), p(2), p(3), p(41), p(42)];
const sched = { "2026-11-18": 1, "2026-11-19": 2, "2026-11-20": 3, "2026-12-28": 42 };
const at = (y: number, m: number, d: number) => new Date(y, m - 1, d, 12);

describe("schedule on the client", () => {
  it("picks the scheduled puzzle for the local date", () => {
    expect(todaysPuzzle(pool, sched, at(2026, 11, 19)).id).toBe(2);
    expect(todaysPuzzle(pool, sched, at(2026, 12, 28)).id).toBe(42);
  });

  it("falls back to cycling when the date is missing or the ID unknown", () => {
    expect(todaysPuzzle(pool, {}, at(2026, 11, 20)).id).toBe(3); // day 2 -> index 2
    expect(todaysPuzzle(pool, { "2026-11-20": 99 }, at(2026, 11, 20)).id).toBe(3);
  });

  it("hides future dailies from random play, keeps unscheduled ones", () => {
    expect(released(pool, sched, at(2026, 11, 19)).map((x) => x.id)).toEqual([1, 2, 41]);
  });

  it("archive lists scheduled puzzles up to today, newest first", () => {
    expect(archive(pool, sched, at(2026, 11, 19)).map((x) => x.id)).toEqual([2, 1]);
    expect(archive(pool, { ...sched, "2026-11-21": 2 }, at(2026, 11, 22)).map((x) => x.id)).toEqual([2, 3, 1]);
  });
});

describe("public/data/schedule.json", () => {
  it("has at least 14 days scheduled from today (run: pnpm data schedule)", () => {
    const s = schedule as Record<string, number>;
    let n = 0;
    for (const d = new Date(); s[localDate(d)] !== undefined; d.setDate(d.getDate() + 1)) n++;
    expect(n).toBeGreaterThanOrEqual(14);
  });

  it("keeps 18 Nov to 27 Dec 2026 on puzzles 1 to 40", () => {
    const s = schedule as Record<string, number>;
    for (let i = 0; i < 40; i++) expect(s[localDate(new Date(2026, 10, 18 + i))]).toBe(i + 1);
  });
});
