import type { LngLat } from "./geo";

export type Puzzle = {
  id: number;
  stat: string;
  prompt: string;
  mechanic: "circle" | "triangle";
  mode: "target" | "budget";
  target_pct: number | null;
  radius_km: number | null;
  perimeter_km: number | null;
  best: { pct: number | null; shape: { center: LngLat; radius_km: number } | { pins: [LngLat, LngLat, LngLat] } | null };
  cells: string;
  stat_file: string | null;
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
  if (p.mechanic === "circle") return `${p.radius_km!.toLocaleString("en")} km circle`;
  return `Max ${p.perimeter_km!.toLocaleString("en")} km around`;
}
