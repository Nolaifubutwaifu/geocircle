"use client";

import { useMemo, useSyncExternalStore } from "react";
import Link from "next/link";
import { archive, goalText, localDate, puzzleHref, type Puzzle, type Schedule } from "@/lib/puzzles";
import { RESULTS_KEY, type Result } from "@/lib/storage";

const noop = () => () => {};

function useResults(): Record<string, Result> {
  // Raw string snapshot keeps the store stable; null on the server.
  const raw = useSyncExternalStore(noop, () => localStorage.getItem(RESULTS_KEY), () => null);
  return useMemo(() => {
    try {
      return JSON.parse(raw ?? "{}");
    } catch {
      return {};
    }
  }, [raw]);
}

function Score({ r }: { r?: Result }) {
  return <span className={`font-display text-[20px] font-bold tabular-nums ${r ? "text-chalk" : "text-mist/50"}`}>{r ? r.score : "Play"}</span>;
}

export default function ArchiveList({ puzzles, schedule }: { puzzles: Puzzle[]; schedule: Schedule }) {
  const results = useResults();
  // The player's local date decides what's out; null on the server, so the list fills in after hydration.
  const today = useSyncExternalStore(noop, () => localDate(), () => null);
  const list = useMemo(() => (today ? archive(puzzles, schedule, new Date()) : []), [puzzles, schedule, today]);
  return (
    <ul className="mt-8 divide-y divide-edge border-y border-edge">
      {list.map((p) => (
        <li key={p.id}>
          <Link href={puzzleHref(p)} className="flex items-center gap-4 py-4 hover:bg-deep/40 focus-visible:bg-deep/40">
            <span className="w-12 font-display text-[15px] font-semibold text-amber">No. {p.id}</span>
            <span className="min-w-0 flex-1">
              <span className="block font-display text-[17px] font-semibold first-letter:uppercase">
                {p.stat}
                {p.scope ? <span className="text-mist">, {p.scope.name}</span> : null}
              </span>
              <span className="block text-[13px] text-mist">
                {p.mechanic === "circle" ? "Circle" : "Triangle"}, {goalText(p).toLowerCase()}
              </span>
            </span>
            <Score r={results[p.id]} />
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function CountryList({ puzzles }: { puzzles: Puzzle[] }) {
  const results = useResults();
  const byContinent = useMemo(() => {
    const m = new Map<string, Puzzle[]>();
    for (const p of puzzles) {
      // Natural Earth files Maldives, Mauritius and Seychelles under "Seven seas (open ocean)".
      const c = p.continent === "Seven seas (open ocean)" ? "Island nations" : (p.continent ?? "Other");
      m.set(c, [...(m.get(c) ?? []), p]);
    }
    return [...m].sort(([a], [b]) => a.localeCompare(b));
  }, [puzzles]);
  const played = puzzles.filter((p) => results[p.id]).length;
  return (
    <>
      <p className="mt-4 font-display text-[15px] text-mist">
        {played} of {puzzles.length} countries halved
      </p>
      {byContinent.map(([continent, list]) => (
        <section key={continent} className="mt-8">
          <h2 className="font-display text-[20px] font-semibold">{continent}</h2>
          <ul className="mt-3 grid grid-cols-1 gap-x-6 border-t border-edge sm:grid-cols-2">
            {list.map((p) => (
              <li key={p.id} className="border-b border-edge">
                <Link href={puzzleHref(p)} className="flex items-center gap-3 py-3 hover:bg-deep/40 focus-visible:bg-deep/40">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-display text-[16px] font-semibold">{p.country}</span>
                    <span className="block text-[12px] text-mist">{(p.population ?? 0).toLocaleString("en")} people</span>
                  </span>
                  <Score r={results[p.id]} />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}
