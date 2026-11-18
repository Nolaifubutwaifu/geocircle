"use client";

import { useMemo, useSyncExternalStore } from "react";
import Link from "next/link";
import { goalText, type Puzzle } from "@/lib/puzzles";
import { RESULTS_KEY, type Result } from "@/lib/storage";

const noop = () => () => {};

export default function ArchiveList({ puzzles }: { puzzles: Puzzle[] }) {
  // Raw string snapshot keeps the store stable; null on the server.
  const raw = useSyncExternalStore(noop, () => localStorage.getItem(RESULTS_KEY), () => null);
  const results = useMemo<Record<string, Result>>(() => {
    try {
      return JSON.parse(raw ?? "{}");
    } catch {
      return {};
    }
  }, [raw]);
  return (
    <ul className="mt-8 divide-y divide-edge border-y border-edge">
      {puzzles.map((p) => {
        const r = results[p.id];
        return (
          <li key={p.id}>
            <Link href={`/play/${p.id}`} className="flex items-center gap-4 py-4 hover:bg-deep/40 focus-visible:bg-deep/40">
              <span className="w-12 font-display text-[15px] font-semibold text-amber">No. {p.id}</span>
              <span className="min-w-0 flex-1">
                <span className="block font-display text-[17px] font-semibold capitalize">{p.stat}</span>
                <span className="block text-[13px] text-mist">
                  {p.mechanic === "circle" ? "Circle" : "Triangle"}, {goalText(p).toLowerCase()}
                </span>
              </span>
              <span className={`font-display text-[20px] font-bold tabular-nums ${r ? "text-chalk" : "text-mist/50"}`}>{r ? r.score : "Play"}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
