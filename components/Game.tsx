"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { cellToLatLng } from "h3-js";
import {
  destinationPoint,
  geodesicCircle,
  greatCircleLine,
  haversineKm,
  perimeterKm,
  shapeRing,
  sideLengthsKm,
  triangleRing,
  validateShape,
  type LngLat,
  type Shape,
} from "@/lib/geo";
import { aggregateTo, capturedSum, classifyGroups, fetchGroups, loadDataset, type Dataset } from "@/lib/cells";
import { scoreBudget, scoreNet, scoreTarget } from "@/lib/score";
import { goalText, puzzleLabel, type Puzzle } from "@/lib/puzzles";
import { readChallenge } from "@/lib/share";
import { track } from "@/lib/analytics";
import { hasSeenHowTo, loadResults, markSeenHowTo, saveResult, type Result } from "@/lib/storage";
import type { Bounds } from "@/lib/land";
import type { Handle, HitTarget, Pill } from "./GameMap";
import HowToPlay from "./HowToPlay";
import RevealPanel from "./RevealPanel";

const GameMap = dynamic(() => import("./GameMap"), { ssr: false });

const km = (n: number) => `${Math.round(n).toLocaleString("en")} km`;

type Phase = "play" | "scoring" | "done";

export default function Game({ puzzle, daily }: { puzzle: Puzzle; daily: boolean }) {
  const isCircle = puzzle.mechanic === "circle";
  const fixedRadius = isCircle && puzzle.mode !== "target" ? puzzle.radius_km : null;

  // Game renders client-only, so the earlier result can seed state directly.
  const [prev] = useState(() => loadResults()[puzzle.id] ?? null);
  const [center, setCenter] = useState<LngLat | null>(prev?.shape.kind === "circle" ? prev.shape.center : null);
  const [radiusKm, setRadiusKm] = useState<number | null>(prev?.shape.kind === "circle" ? prev.shape.radiusKm : fixedRadius);
  const [pins, setPins] = useState<LngLat[]>(prev?.shape.kind === "triangle" ? prev.shape.pins : []);
  const [selectedPin, setSelectedPin] = useState(0);
  const [phase, setPhase] = useState<Phase>(prev ? "done" : "play");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(prev);
  const [projection, setProjection] = useState<"mercator" | "globe">("globe");
  const [howTo, setHowTo] = useState(() => !hasSeenHowTo());
  const [heat, setHeat] = useState<{ id: string; v: number }[] | null>(null);
  const [challenge] = useState(() => readChallenge(window.location.search)); // a friend's ?beat=64&s=... link
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => {
    if (challenge) track("challenge_opened", { puzzle: puzzle.id, beat: challenge.score });
  }, [challenge, puzzle.id]);
  const ds = useRef<Promise<Dataset> | null>(null);
  const vsDs = useRef<Promise<Dataset> | null>(null); // the "people" side of net puzzles
  const view = useRef<{ res: number; bounds: Bounds } | null>(null);

  // Load the puzzle's data.
  useEffect(() => {
    const scope = puzzle.scope?.countries ?? null;
    ds.current = loadDataset(puzzle.cells, puzzle.stat_file, scope);
    vsDs.current = puzzle.vs ? loadDataset(puzzle.vs.cells, null, scope) : null;
    Promise.all([ds.current, vsDs.current]).catch(() => setError("Couldn't load this puzzle's data. Reload the page to try again."));
  }, [puzzle]);

  const shape: Shape | null = useMemo(() => {
    if (isCircle) return center && radiusKm ? { kind: "circle", center, radiusKm } : null;
    return pins.length === 3 ? { kind: "triangle", pins: pins as [LngLat, LngLat, LngLat] } : null;
  }, [isCircle, center, radiusKm, pins]);

  const problem = shape ? validateShape(shape, puzzle.mode === "budget" && !isCircle ? puzzle.perimeter_km : null) : null;

  // Prefetch the fine data along the shape edge while the player adjusts it.
  useEffect(() => {
    if (!shape || problem || phase !== "play") return;
    const t = setTimeout(async () => {
      for (const p of [ds.current, vsDs.current]) {
        const d = await p;
        if (d) fetchGroups(d, classifyGroups(shape, d).edge).catch(() => {});
      }
    }, 400);
    return () => clearTimeout(t);
  }, [shape, problem, phase]);

  const submit = useCallback(async () => {
    if (phase !== "play") return;
    if (!shape) {
      setError(isCircle ? "Drop the centre and drag out your circle first" : `Place ${3 - pins.length} more pin${pins.length === 2 ? "" : "s"}`);
      return;
    }
    if (problem) {
      setError(problem);
      return;
    }
    setPhase("scoring");
    try {
      const d = await ds.current!;
      const a = await capturedSum(shape, d);
      const pct = d.total > 0 ? (100 * a) / d.total : 0;
      let score: number;
      let counts: Result["counts"];
      if (puzzle.mode === "net") {
        const b = await capturedSum(shape, (await vsDs.current)!);
        counts = { a, b };
        score = scoreNet(a - b, puzzle.best.net ?? a - b);
      } else score = puzzle.mode === "target" ? scoreTarget(pct, puzzle.target_pct!) : scoreBudget(pct, puzzle.best.pct ?? pct);
      const r: Result = { score, pct, shape, playedAt: new Date().toISOString(), counts };
      saveResult(puzzle.id, r);
      track("puzzle_submitted", {
        puzzle: puzzle.id,
        key: puzzle.key,
        daily,
        mechanic: puzzle.mechanic,
        mode: puzzle.mode,
        difficulty: puzzle.difficulty,
        score,
        challenged: !!challenge,
      });
      setResult(r);
      setPhase("done");
    } catch {
      setPhase("play");
      setError("Scoring failed while loading data. Check your connection and submit again.");
    }
  }, [phase, shape, problem, isCircle, pins.length, puzzle, daily, challenge]);

  // Heat reveal for whatever part of the map is in view.
  const refreshHeat = useCallback(async () => {
    const v = view.current;
    if (!v || !ds.current) return;
    const d = await ds.current;
    const wrap = (x: number) => ((((x - v.bounds.west) % 360) + 360) % 360);
    const width = Math.min(360, v.bounds.east - v.bounds.west);
    const keys = [...d.r3Totals.keys()].filter((k) => {
      const [lat, lng] = cellToLatLng(k);
      return lat >= v.bounds.south - 2 && lat <= v.bounds.north + 2 && wrap(lng) <= width + 2;
    });
    let res = v.res;
    if (res >= 4 && keys.length <= 2500) await fetchGroups(d, keys);
    else res = Math.min(res, 3);
    const agg = aggregateTo(d, res, keys);
    setHeat([...agg].filter(([, val]) => val > 0).map(([id, val]) => ({ id, v: val })));
  }, []);

  useEffect(() => {
    if (phase === "done") refreshHeat();
  }, [phase, refreshHeat]);

  const onView = useCallback(
    (res: number, bounds: Bounds) => {
      view.current = { res, bounds };
      if (phase === "done") refreshHeat();
    },
    [phase, refreshHeat],
  );

  const play = phase === "play";

  const onTap = useCallback(
    (p: LngLat) => {
      if (!play) return;
      setError(null);
      if (isCircle) {
        if (!center || fixedRadius) setCenter(p);
      } else if (pins.length < 3) {
        setPins((ps) => [...ps, p]);
        setSelectedPin(pins.length);
      }
    },
    [play, isCircle, center, fixedRadius, pins.length],
  );

  const onDrag = useCallback(
    (hit: HitTarget, p: LngLat, ph: "start" | "move" | "end") => {
      if (!play) return;
      setError(null);
      if (hit.kind === "handle" && hit.id === "center") setCenter(p);
      else if (hit.kind === "ring" || hit.kind === "sizing" || (hit.kind === "handle" && hit.id === "edge")) {
        if (!center || fixedRadius) return;
        const r = haversineKm(center, p);
        setRadiusKm(ph === "end" && r < 10 && hit.kind === "sizing" ? null : r);
      } else if (hit.kind === "handle" && hit.id.startsWith("pin")) {
        const i = Number(hit.id.slice(3));
        setSelectedPin(i);
        setPins((ps) => ps.map((q, j) => (j === i ? p : q)));
      }
    },
    [play, center, fixedRadius],
  );

  const reset = () => {
    setCenter(null);
    setRadiusKm(fixedRadius);
    setPins([]);
    setError(null);
  };

  // Keyboard play: arrows move the centre or selected pin, + and - resize, 1 to 3 pick a pin, Enter submits.
  const onKey = (e: React.KeyboardEvent) => {
    if (!play) return;
    const step = e.shiftKey ? 2 : 0.5;
    const mv: Record<string, [number, number]> = { ArrowUp: [0, step], ArrowDown: [0, -step], ArrowLeft: [-step, 0], ArrowRight: [step, 0] };
    const shift = (q: LngLat): LngLat => [q[0] + mv[e.key][0], Math.max(-84, Math.min(84, q[1] + mv[e.key][1]))];
    if (mv[e.key]) {
      e.preventDefault();
      if (isCircle && center) setCenter(shift(center));
      else if (!isCircle && pins[selectedPin]) setPins((ps) => ps.map((q, j) => (j === selectedPin ? shift(q) : q)));
    } else if ((e.key === "+" || e.key === "=") && isCircle && radiusKm && !fixedRadius) setRadiusKm(Math.min(5000, radiusKm * 1.1));
    else if (e.key === "-" && isCircle && radiusKm && !fixedRadius) setRadiusKm(Math.max(10, radiusKm / 1.1));
    else if (["1", "2", "3"].includes(e.key)) setSelectedPin(Number(e.key) - 1);
    else if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  };

  // What the map draws.
  const ring = shape ? shapeRing(shape) : null;
  const edgePoint = center && radiusKm ? destinationPoint(center, 90, radiusKm) : null;
  const handles: Handle[] = useMemo(() => {
    if (!play) return [];
    if (isCircle) {
      const h: Handle[] = [];
      if (center) h.push({ id: "center", at: center });
      if (edgePoint && !fixedRadius) h.push({ id: "edge", at: edgePoint });
      return h;
    }
    return pins.map((p, i) => ({ id: `pin${i}`, at: p }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [play, isCircle, center, edgePoint?.[0], edgePoint?.[1], fixedRadius, pins]);

  const pills: Pill[] = useMemo(() => {
    if (!play) return [];
    if (isCircle && edgePoint && radiusKm) return [{ id: "r", at: edgePoint, text: km(radiusKm), tone: problem ? "bad" : "ok" }];
    if (!isCircle && pins.length >= 2) {
      const sides = pins.length === 3 ? sideLengthsKm(pins as [LngLat, LngLat, LngLat]) : [haversineKm(pins[0], pins[1])];
      return sides.map((s, i) => {
        const a = pins[i];
        const b = pins[(i + 1) % pins.length];
        const mid = greatCircleLine(a, b, 2)[1];
        return { id: `s${i}`, at: mid, text: km(s), tone: problem ? "bad" : "ok" };
      });
    }
    return [];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [play, isCircle, edgePoint?.[0], edgePoint?.[1], radiusKm, pins, problem]);

  const openPins = !isCircle && pins.length === 2 ? greatCircleLine(pins[0], pins[1]) : [];
  const best = phase === "done" && puzzle.best.shape
    ? "center" in puzzle.best.shape
      ? geodesicCircle(puzzle.best.shape.center, puzzle.best.shape.radius_km)
      : triangleRing(puzzle.best.shape.pins)
    : null;

  let hint: string;
  if (isCircle) {
    if (!center) hint = "Tap the map to drop the centre";
    else if (!radiusKm) hint = "Now drag outward to size your circle";
    else hint = fixedRadius ? "Drag or tap to move it" : "Drag the centre or edge to adjust";
  } else hint = pins.length < 3 ? `Tap to place pin ${pins.length + 1} of 3` : "Drag any pin to adjust";

  const readout = isCircle
    ? radiusKm && center
      ? `Radius ${km(radiusKm)}`
      : null
    : pins.length === 3
      ? `${km(perimeterKm(pins))} around${puzzle.perimeter_km ? ` of ${km(puzzle.perimeter_km)}` : ""}`
      : null;

  return (
    <main className="fixed inset-0 overflow-hidden bg-ocean text-chalk" onKeyDown={onKey}>
      <GameMap
        view={puzzle.view}
        projection={projection}
        shape={ring}
        shapeBad={!!problem}
        pins={openPins}
        best={best}
        friend={phase === "done" && challenge?.shape ? shapeRing(challenge.shape) : null}
        handles={handles}
        pills={pills}
        ring={isCircle && center && radiusKm && !fixedRadius && play ? { center, radiusKm } : null}
        captureAll={play && isCircle && !!center && !radiusKm}
        heat={phase === "done" ? heat : null}
        dimLand={phase === "done"}
        interactive={play}
        bottomInset={phase === "done" ? (collapsed ? 70 : 230) : 0}
        onTap={onTap}
        onDrag={onDrag}
        onView={onView}
      />

      <header className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-3 p-3 pt-[max(0.75rem,env(safe-area-inset-top))] sm:p-5 sm:pt-[max(1.25rem,env(safe-area-inset-top))]">
        <section className="chamfer pointer-events-auto min-w-0 max-w-md flex-1 sm:flex-none" aria-live="polite">
          <div className="chamfer-in px-4 py-3 sm:px-5 sm:py-4">
            <div className="flex items-center gap-2 whitespace-nowrap text-[13px] text-mist">
              <Link href={daily || typeof puzzle.id === "number" ? "/archive" : "/countries"} className="font-display font-semibold text-amber hover:underline">
                {puzzleLabel(puzzle)}
              </Link>
              <span className="hidden sm:inline">{daily ? "Today's puzzle" : typeof puzzle.id === "number" ? "From the archive" : "Halve the country"}</span>
              <span className="ml-auto rounded-sm bg-deep px-1.5 py-0.5 font-display text-[12px] text-chalk">{goalText(puzzle)}</span>
            </div>
            <h1 className="mt-1.5 font-display text-[17px] font-semibold leading-snug text-balance sm:text-[22px]">{puzzle.prompt}</h1>
            {challenge && phase !== "done" && (
              <p className="mt-1.5 font-display text-[14px] font-semibold text-amber">A friend scored {challenge.score}. Can you beat it?</p>
            )}
          </div>
        </section>
        <div className="pointer-events-auto flex flex-col items-end gap-2">
          <div className="seg" role="group" aria-label="Map view">
            {(["mercator", "globe"] as const).map((p) => (
              <button key={p} aria-pressed={projection === p} onClick={() => setProjection(p)}>
                {p === "mercator" ? "Flat" : "Globe"}
              </button>
            ))}
          </div>
          <button className="icon-btn" onClick={() => setHowTo(true)} aria-label="How to play">
            ?
          </button>
        </div>
      </header>

      {phase !== "done" && (
        <footer className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:p-5 sm:pb-[max(1.25rem,env(safe-area-inset-bottom))]">
          <div className="chamfer pointer-events-auto w-full max-w-xl">
            <div className="chamfer-in flex items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <p className={`text-[14px] leading-snug sm:text-[15px] ${error ? "text-bad" : "text-chalk"}`} role={error ? "alert" : undefined}>
                  {error ?? problem ?? hint}
                </p>
                {readout && <p className="font-display text-[13px] text-mist">{readout}</p>}
              </div>
              {(center || pins.length > 0) && play && (
                <button className="ghost-btn" onClick={reset}>
                  Reset
                </button>
              )}
              <button className="primary-btn" onClick={submit} disabled={phase === "scoring"}>
                {phase === "scoring" ? "Scoring…" : isCircle ? "Submit circle" : "Submit triangle"}
              </button>
            </div>
          </div>
        </footer>
      )}

      {phase === "done" && result && (
        <RevealPanel
          puzzle={puzzle}
          result={result}
          daily={daily}
          friendScore={challenge?.score ?? null}
          collapsed={collapsed}
          onCollapse={setCollapsed}
        />
      )}
      {howTo && (
        <HowToPlay
          mechanic={puzzle.mechanic}
          onClose={() => {
            markSeenHowTo();
            setHowTo(false);
          }}
        />
      )}
    </main>
  );
}
