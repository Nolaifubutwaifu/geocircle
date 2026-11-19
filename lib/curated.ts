// Review decisions stored in puzzles/curated.json (PRD §6). Used by the dev-only review page.
import { localDate, type Puzzle } from "./puzzles";

export type Candidate = Omit<Puzzle, "id"> & {
  key: string;
  dataset: string;
  rank: number;
  difficulty: "easy" | "medium" | "hard";
  inputs_hash: string;
  checks: {
    cells: number;
    top_cell_share: number;
    surprise: number;
    reach_pct: number | null;
    smallest_km: number | null;
    best_pct: number | null;
    best_net: number | null;
    naive_median: number;
    passed: boolean;
    failed: string[];
  };
};

export const REJECT_REASONS = ["boring", "too hard", "broken", "wrong data"] as const;

export type CuratedEntry =
  | {
      status: "approved";
      id: number;
      fact: string;
      prompt: string | null; // null: use the generated prompt
      reason: null;
      decided: string;
      /** The candidate as reviewed, so publish works without the git-ignored candidates.json. */
      puzzle: Omit<Candidate, "checks" | "rank" | "inputs_hash" | "dataset">;
    }
  | { status: "rejected"; reason: (typeof REJECT_REASONS)[number]; note: string | null; decided: string };

export type Curated = Record<string, CuratedEntry>;

export type Decision =
  | { key: string; status: "approved"; fact: string; prompt: string }
  | { key: string; status: "rejected"; reason: string; note?: string };

/** Next free integer ID: above every published and every curated ID, never reused. */
export function nextId(curated: Curated, publishedIds: (number | string)[]): number {
  const ids = [...publishedIds, ...Object.values(curated).map((e) => (e.status === "approved" ? e.id : 0))];
  return Math.max(0, ...ids.filter((x): x is number => typeof x === "number")) + 1;
}

/** Applies one decision. Throws on bad input; returns the new curated record. */
export function decide(curated: Curated, c: Candidate | undefined, d: Decision, publishedIds: (number | string)[], now = new Date()): Curated {
  if (!c) throw new Error(`unknown candidate ${d.key}`);
  if (curated[d.key]) throw new Error(`${d.key} is already ${curated[d.key].status}`);
  const decided = localDate(now);
  if (d.status === "approved") {
    const fact = d.fact.trim();
    const prompt = d.prompt.trim();
    if (!fact) throw new Error("fact is empty");
    if (!prompt) throw new Error("prompt is empty");
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { checks, rank, inputs_hash, dataset, ...puzzle } = c;
    return {
      ...curated,
      [d.key]: { status: "approved", id: nextId(curated, publishedIds), fact, prompt: prompt === c.prompt ? null : prompt, reason: null, decided, puzzle },
    };
  }
  if (d.status === "rejected") {
    if (!REJECT_REASONS.includes(d.reason as never)) throw new Error(`reason must be one of ${REJECT_REASONS.join(", ")}`);
    return { ...curated, [d.key]: { status: "rejected", reason: d.reason as (typeof REJECT_REASONS)[number], note: d.note?.trim() || null, decided } };
  }
  throw new Error("status must be approved or rejected");
}
