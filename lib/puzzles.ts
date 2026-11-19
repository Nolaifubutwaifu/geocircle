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
  /** Generated puzzles: candidate key and simulated difficulty. */
  key?: string;
  difficulty?: "easy" | "medium" | "hard";
};

/** First daily puzzle. Day n after this shows puzzle n (cycling). */
export const LAUNCH_DAY = "2026-11-18";

export function dayIndex(now = new Date()): number {
  const local = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const [y, m, d] = LAUNCH_DAY.split("-").map(Number);
  return Math.max(0, Math.round((local.getTime() - new Date(y, m - 1, d).getTime()) / 86_400_000));
}

/** Local date to puzzle ID, from public/data/schedule.json (PRD §5.9). */
export type Schedule = Record<string, number>;

/** "2026-11-19" in the player's time zone. */
export const localDate = (d = new Date()) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** The scheduled puzzle for today; cycles through all puzzles if the date isn't scheduled, so the site never breaks. */
export function todaysPuzzle(puzzles: Puzzle[], schedule: Schedule = {}, now = new Date()): Puzzle {
  const id = schedule[localDate(now)];
  return puzzles.find((p) => p.id === id) ?? puzzles[dayIndex(now) % puzzles.length];
}

/** Puzzles a player may open: scheduled on or before today, or never scheduled. Future dailies stay hidden. */
export function released(puzzles: Puzzle[], schedule: Schedule, now = new Date()): Puzzle[] {
  const today = localDate(now);
  const future = new Set(Object.entries(schedule).filter(([d]) => d > today).map(([, id]) => id));
  return puzzles.filter((p) => !future.has(p.id as number));
}

/** Puzzles scheduled on or before today, newest first, each once. */
export function archive(puzzles: Puzzle[], schedule: Schedule, now = new Date()): Puzzle[] {
  const today = localDate(now);
  const byId = new Map(puzzles.map((p) => [p.id, p]));
  const ids = Object.entries(schedule)
    .filter(([d]) => d <= today)
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([, id]) => id);
  return [...new Set(ids)].flatMap((id) => byId.get(id) ?? []);
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

/** A random other puzzle, preferring ones not played yet. Null if the pool has nothing else. */
export function pickRandom(pool: Puzzle[], currentId: Puzzle["id"], played: Set<string>, rand = Math.random): Puzzle | null {
  const others = pool.filter((p) => String(p.id) !== String(currentId));
  const fresh = others.filter((p) => !played.has(String(p.id)));
  const from = fresh.length ? fresh : others;
  return from.length ? from[Math.floor(rand() * from.length)] : null;
}
