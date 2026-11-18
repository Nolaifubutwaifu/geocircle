// Results and first-visit flag in localStorage.
import type { Shape } from "./geo";

export type Result = {
  score: number;
  pct: number;
  shape: Shape;
  playedAt: string;
  /** Net puzzles: what the shape caught of each side. */
  counts?: { a: number; b: number };
};
export const RESULTS_KEY = "geocircle:v1:results";
const SEEN = "geocircle:v1:seen-how-to";

export function loadResults(): Record<string, Result> {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(localStorage.getItem(RESULTS_KEY) ?? "{}");
  } catch {
    return {};
  }
}

export function saveResult(id: number | string, r: Result) {
  const all = loadResults();
  all[id] = r;
  localStorage.setItem(RESULTS_KEY, JSON.stringify(all));
}

export const hasSeenHowTo = () => typeof window !== "undefined" && localStorage.getItem(SEEN) === "1";
export const markSeenHowTo = () => localStorage.setItem(SEEN, "1");
