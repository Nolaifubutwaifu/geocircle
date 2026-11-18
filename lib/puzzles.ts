import type { LngLat } from "./geo";

export type Puzzle = {
  id: number | string; // numbers for the daily set, "c-aus" style for "halve the country" puzzles
  stat: string;
  prompt: string;
  mechanic: "circle" | "triangle";
  /** target: hit a %. budget: catch the most with a fixed size. net: catch the most of `stat` minus `vs`. */
  mode: "target" | "budget" | "net";
  target_pct: number | null;
  radius_km: number | null;
  perimeter_km: number | null;
  best: {
    pct: number | null;
    net?: number | null;
    shape: { center: LngLat; radius_km: number } | { pins: [LngLat, LngLat, LngLat] } | null;
  };
  cells: string;
  stat_file: string | null;
  /** Limits the puzzle to some countries (Natural Earth country indexes). */
  scope?: { name: string; countries: number[] } | null;
  vs?: { cells: string; label: string } | null;
  country?: string;
  code?: string;
  continent?: string;
  population?: number;
  view: { center: LngLat; zoom: number };
  fact: string;
  credit: string;
};

/** First daily puzzle. Day n after this shows puzzle n (cycling). */
export const LAUNCH_DAY = "2026-11-18";

export function dayIndex(now = new Date()): number {
  const local = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const [y, m, d] = LAUNCH_DAY.split("-").map(Number);
  return Math.max(0, Math.round((local.getTime() - new Date(y, m - 1, d).getTime()) / 86_400_000));
}

export function todaysPuzzle(puzzles: Puzzle[], now = new Date()): Puzzle {
  return puzzles[dayIndex(now) % puzzles.length];
}

export function goalText(p: Puzzle): string {
  if (p.mode === "target") return `Target ${p.target_pct}%`;
  if (p.mode === "net") return `${p.radius_km!.toLocaleString("en")} km, ${p.stat} vs ${p.vs?.label}`;
  if (p.mechanic === "circle") return `${p.radius_km!.toLocaleString("en")} km circle`;
  return `Max ${p.perimeter_km!.toLocaleString("en")} km around`;
}

/** "No. 12" for daily puzzles, the country name for "halve the country" puzzles. */
export function puzzleLabel(p: Puzzle): string {
  return typeof p.id === "number" ? `No. ${p.id}` : p.country ?? String(p.id);
}

export function puzzleHref(p: Puzzle): string {
  return typeof p.id === "number" ? `/play/${p.id}` : `/country/${p.code!.toLowerCase()}`;
}

/** "the world's rice eaten", "Australia's people". */
export function whose(p: Puzzle): string {
  const owner = p.scope ? `${p.scope.name}'s` : "the world's";
  return `${owner} ${p.stat}`;
}
