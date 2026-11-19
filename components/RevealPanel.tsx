"use client";

import { useEffect, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { pickRandom, puzzleHref, puzzleLabel, whose, type Puzzle } from "@/lib/puzzles";
import { loadResults, type Result } from "@/lib/storage";

function useCountUp(target: number, ms = 1400) {
  const [v, setV] = useState(0);
  useEffect(() => {
    const instant = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const k = instant ? 1 : Math.min(1, (t - t0) / ms);
      setV(target * (1 - Math.pow(1 - k, 3)));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return v;
}

function count(x: number): string {
  if (x >= 1e9) return `${(x / 1e9).toFixed(1)} billion`;
  if (x >= 1e6) return `${(x / 1e6).toFixed(1)} million`;
  return Math.round(x).toLocaleString("en");
}

export default function RevealPanel({ puzzle, result }: { puzzle: Puzzle; result: Result }) {
  const score = useCountUp(result.score);
  const [copied, setCopied] = useState(false);
  // A transition, so "Loading…" clears itself even when Next keeps this page around for the back button.
  const [jumping, startJump] = useTransition();
  const router = useRouter();

  // Country puzzles jump to another country, daily puzzles to another daily one.
  function randomPuzzle() {
    startJump(async () => {
      try {
        const pool: Puzzle[] = await fetch(typeof puzzle.id === "number" ? "/data/puzzles.json" : "/data/countries-mode.json").then((r) => r.json());
        const next = pickRandom(pool, puzzle.id, new Set(Object.keys(loadResults())));
        if (next) router.push(puzzleHref(next));
      } catch {
        /* offline: stay on this puzzle */
      }
    });
  }
  const pct = result.pct.toFixed(1);

  const caught = puzzle.stat === "land" ? "covered" : "caught";
  let detail: string;
  if (puzzle.mode === "target") detail = `You ${caught} ${pct}% of ${whose(puzzle)}. The target was ${puzzle.target_pct}%.`;
  else if (puzzle.mode === "budget") detail = `You ${caught} ${pct}%. The best spot we found catches ${puzzle.best.pct?.toFixed(1)}%, drawn dashed on the map.`;
  else {
    const { a, b } = result.counts ?? { a: 0, b: 0 };
    const vs = puzzle.vs?.label ?? "people";
    const margin = a - b;
    const verdict = margin > 0 ? `${puzzle.stat} win by ${count(margin)}` : `${vs} win by ${count(-margin)}`;
    detail = `Your circle holds ${count(a)} ${puzzle.stat} and ${count(b)} ${vs}: ${verdict}. At the best spot we found, ${puzzle.stat} win by ${count(puzzle.best.net ?? 0)} (dashed circle).`;
  }

  async function share() {
    const icon = puzzle.mechanic === "circle" ? "⭕" : "🔺";
    const text = `Geocircle ${puzzleLabel(puzzle)} ${icon} ${result.score}/100\n${location.origin}${puzzleHref(puzzle)}`;
    try {
      if (navigator.share && matchMedia("(pointer: coarse)").matches) await navigator.share({ text });
      else {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }
    } catch {
      /* share sheet dismissed */
    }
  }

  return (
    <section className="reveal pointer-events-none absolute inset-x-0 bottom-0 flex justify-center p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:p-5 sm:pb-[max(1.25rem,env(safe-area-inset-bottom))]" aria-live="polite">
      <div className="chamfer pointer-events-auto w-full max-w-xl">
        <div className="chamfer-in px-5 py-4 sm:px-6 sm:py-5">
          <div className="flex items-end gap-4">
            <p className="font-display text-[64px] font-bold leading-none text-amber tabular-nums sm:text-[76px]" aria-label={`Score ${result.score} out of 100`}>
              {Math.round(score)}
            </p>
            <p className="whitespace-nowrap pb-2 font-display text-[18px] text-mist">/ 100</p>
            <div className="ml-auto flex flex-wrap justify-end gap-2 pb-1">
              <button className="outline-btn" onClick={share}>
                {copied ? "Copied" : "Share"}
              </button>
              <button className="primary-btn" onClick={randomPuzzle} disabled={jumping}>
                {jumping ? "Loading…" : typeof puzzle.id === "number" ? "Random puzzle" : "Random country"}
              </button>
            </div>
          </div>
          <p className="mt-3 text-[15px] leading-relaxed text-chalk">{detail}</p>
          <p className="mt-2 text-[14px] leading-relaxed text-mist">{puzzle.fact}</p>
          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px]">
            <Link href="/archive" className="text-amber underline-offset-4 hover:underline">
              All puzzles
            </Link>
            <Link href="/countries" className="text-amber underline-offset-4 hover:underline">
              Halve a country
            </Link>
            <span className="text-mist">Zoom in to see where it all is.</span>
            <span className="ml-auto text-[12px] text-mist/80">Data: {puzzle.credit}</span>
          </div>
        </div>
      </div>
    </section>
  );
}
