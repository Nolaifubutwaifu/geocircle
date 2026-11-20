"use client";

import { useEffect, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import schedule from "@/public/data/schedule.json";
import { pickNext, puzzleHref, puzzleLabel, released, whose, type Puzzle, type Schedule } from "@/lib/puzzles";
import { encodeShape } from "@/lib/share";
import { playerStats, untilMidnight } from "@/lib/stats";
import { loadResults, saveResult, type Feel, type Result } from "@/lib/storage";

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

/** Re-renders every 30 s, for the countdown. */
function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function count(x: number): string {
  if (x >= 1e9) return `${(x / 1e9).toFixed(1)} billion`;
  if (x >= 1e6) return `${(x / 1e6).toFixed(1)} million`;
  return Math.round(x).toLocaleString("en");
}

const FEELS: Feel[] = ["too easy", "just right", "too hard"];

type Props = {
  puzzle: Puzzle;
  result: Result;
  daily: boolean;
  friendScore: number | null;
  collapsed: boolean;
  onCollapse: (c: boolean) => void;
};

export default function RevealPanel({ puzzle, result, daily, friendScore, collapsed, onCollapse }: Props) {
  const score = useCountUp(result.score);
  const now = useClock();
  const [copied, setCopied] = useState(false);
  const [feel, setFeel] = useState<Feel | undefined>(result.feel);
  // A transition, so "Loading…" clears itself even when Next keeps this page around for the back button.
  const [jumping, startJump] = useTransition();
  const [allPlayed, setAllPlayed] = useState(false);
  const router = useRouter();
  const numbered = typeof puzzle.id === "number";
  const stats = numbered ? playerStats(loadResults(), schedule as Schedule, now) : null;

  // Country puzzles jump to another country, daily puzzles to another daily one.
  function nextPuzzle() {
    startJump(async () => {
      try {
        const [pool, sched]: [Puzzle[], Schedule] = await Promise.all([
          fetch(numbered ? "/data/puzzles.json" : "/data/countries-mode.json").then((r) => r.json()),
          numbered ? fetch("/data/schedule.json").then((r) => r.json()) : {},
        ]);
        // Never jump to a future daily puzzle.
        const next = pickNext(released(pool, sched), puzzle.id, new Set(Object.keys(loadResults())));
        if (next) router.push(puzzleHref(next));
        else setAllPlayed(true);
      } catch {
        /* offline: stay on this puzzle */
      }
    });
  }

  function rate(f: Feel) {
    setFeel(f);
    saveResult(puzzle.id, { ...result, feel: f });
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
  const versus =
    friendScore === null
      ? null
      : result.score > friendScore
        ? `You beat your friend's ${friendScore}.`
        : result.score === friendScore
          ? `You tied with your friend's ${friendScore}.`
          : `Your friend's ${friendScore} still stands (their shape is dotted).`;

  async function share() {
    const icon = puzzle.mechanic === "circle" ? "⭕" : "🔺";
    const streak = daily && stats && stats.streak >= 2 ? ` 🔥${stats.streak}` : "";
    const topic = whose(puzzle).replace(/^./, (c) => c.toUpperCase());
    const url = `${location.origin}${puzzleHref(puzzle)}?beat=${result.score}&s=${encodeShape(result.shape)}`;
    const text = `Geocircle ${puzzleLabel(puzzle)} ${icon} ${result.score}/100${streak}\n${topic}. Can you beat ${result.score}?\n${url}`;
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

  const next = (
    <button className="primary-btn" onClick={nextPuzzle} disabled={jumping || allPlayed}>
      {allPlayed ? "All played" : jumping ? "Loading…" : numbered ? "Next puzzle" : "Next country"}
    </button>
  );
  const buttons = (
    <div className="ml-auto flex flex-wrap justify-end gap-1.5 sm:gap-2">
      <button className="outline-btn" onClick={share}>
        {copied ? "Copied" : "Share"}
      </button>
      {next}
    </div>
  );

  return (
    <section className="reveal pointer-events-none absolute inset-x-0 bottom-0 flex justify-center p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:p-5 sm:pb-[max(1.25rem,env(safe-area-inset-bottom))]" aria-live="polite">
      <div className="chamfer pointer-events-auto w-full max-w-xl">
        {collapsed ? (
          <div className="chamfer-in flex items-center gap-2 px-4 py-2.5">
            <p className="whitespace-nowrap font-display text-[28px] font-bold leading-none text-amber tabular-nums">
              {result.score}
              <span className="text-[14px] font-normal text-mist"> / 100</span>
            </p>
            <button className="ghost-btn whitespace-nowrap" onClick={() => onCollapse(false)} aria-expanded={false}>
              Details ▴
            </button>
            <div className="ml-auto">{next}</div>
          </div>
        ) : (
          <div className="chamfer-in relative px-5 py-4 sm:px-6 sm:py-5">
            <button
              className="absolute right-3 top-1.5 text-[12px] text-mist underline-offset-4 hover:text-chalk hover:underline"
              onClick={() => onCollapse(true)}
              aria-expanded
            >
              Hide ▾
            </button>
            <div className="mt-2 flex items-end gap-2 sm:gap-4">
              <p className="font-display text-[46px] font-bold leading-none text-amber tabular-nums sm:text-[76px]" aria-label={`Score ${result.score} out of 100`}>
                {Math.round(score)}
              </p>
              <p className="whitespace-nowrap pb-1.5 font-display text-[16px] text-mist sm:pb-2 sm:text-[18px]">/ 100</p>
              <div className="ml-auto pb-1">{buttons}</div>
            </div>
            {versus && <p className="mt-3 font-display text-[15px] font-semibold text-amber">{versus}</p>}
            <p className="mt-3 text-[15px] leading-relaxed text-chalk">{detail}</p>
            {/* One fact only: the sourced fun fact beats the generated one. */}
            {puzzle.funfact ? (
              <p className="mt-2 text-[14px] leading-relaxed text-chalk">
                <span className="font-display font-semibold text-amber">Did you know? </span>
                {puzzle.funfact.text}{" "}
                <a href={puzzle.funfact.source} target="_blank" rel="noopener noreferrer" className="text-[12px] text-mist underline underline-offset-2 hover:text-chalk">
                  Source
                </a>
              </p>
            ) : (
              <p className="mt-2 text-[14px] leading-relaxed text-mist">{puzzle.fact}</p>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
              <span className="text-[13px] text-mist">{feel ? "Thanks!" : "How hard?"}</span>
              <div className="seg seg-sm" role="group" aria-label="How hard was it?">
                {FEELS.map((f) => (
                  <button key={f} aria-pressed={feel === f} onClick={() => rate(f)}>
                    {f[0].toUpperCase() + f.slice(1)}
                  </button>
                ))}
              </div>
            </div>
            {stats && (
              <p className="mt-3 font-display text-[13px] text-mist">
                {stats.streak > 0 && <span className="text-chalk">🔥 {stats.streak}-day streak · </span>}
                {stats.played} played · average {stats.avg}
                {stats.best > 1 && ` · best streak ${stats.best}`}
                {daily && ` · next daily in ${untilMidnight(now)}`}
              </p>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px]">
              <Link href="/archive" className="text-amber underline-offset-4 hover:underline">
                All puzzles
              </Link>
              <Link href="/countries" className="text-amber underline-offset-4 hover:underline">
                Halve a country
              </Link>
              <span className="ml-auto text-[12px] text-mist/80">Data: {puzzle.credit}</span>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
