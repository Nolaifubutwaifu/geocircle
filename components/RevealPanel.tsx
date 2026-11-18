"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { Puzzle } from "@/lib/puzzles";
import type { Result } from "@/lib/storage";

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

export default function RevealPanel({ puzzle, result }: { puzzle: Puzzle; result: Result }) {
  const score = useCountUp(result.score);
  const [copied, setCopied] = useState(false);
  const pct = result.pct.toFixed(1);

  const detail =
    puzzle.mode === "target"
      ? `You caught ${pct}% of the world's ${puzzle.stat}. The target was ${puzzle.target_pct}%.`
      : `You caught ${pct}%. The best spot we found catches ${puzzle.best.pct?.toFixed(1)}%, drawn dashed on the map.`;

  async function share() {
    const icon = puzzle.mechanic === "circle" ? "⭕" : "🔺";
    const text = `Geocircle No. ${puzzle.id} ${icon} ${result.score}/100\n${location.origin}/play/${puzzle.id}`;
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
    <section className="reveal pointer-events-none absolute inset-x-0 bottom-0 flex justify-center p-3 sm:p-5" aria-live="polite">
      <div className="chamfer pointer-events-auto w-full max-w-xl">
        <div className="chamfer-in px-5 py-4 sm:px-6 sm:py-5">
          <div className="flex items-end gap-4">
            <p className="font-display text-[64px] font-bold leading-none text-amber tabular-nums sm:text-[76px]" aria-label={`Score ${result.score} out of 100`}>
              {Math.round(score)}
            </p>
            <p className="pb-2 font-display text-[18px] text-mist">/ 100</p>
            <div className="ml-auto flex gap-2 pb-1">
              <button className="primary-btn" onClick={share}>
                {copied ? "Copied" : "Share"}
              </button>
            </div>
          </div>
          <p className="mt-3 text-[15px] leading-relaxed text-chalk">{detail}</p>
          <p className="mt-2 text-[14px] leading-relaxed text-mist">{puzzle.fact}</p>
          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px]">
            <Link href="/archive" className="text-amber underline-offset-4 hover:underline">
              All puzzles
            </Link>
            <span className="text-mist">Zoom in to see where it all is.</span>
            <span className="ml-auto text-[12px] text-mist/80">Data: {puzzle.credit}</span>
          </div>
        </div>
      </div>
    </section>
  );
}
