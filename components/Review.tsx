"use client";

import { useEffect, useMemo, useState } from "react";
import Game from "./Game";
import { REJECT_REASONS, type Candidate, type Decision } from "@/lib/curated";
import type { Puzzle } from "@/lib/puzzles";

const FILTERS = ["dataset", "scope", "mechanic", "mode", "difficulty"] as const;
type Filter = (typeof FILTERS)[number];

const field = (c: Candidate, f: Filter) => (f === "scope" ? c.key.split("|")[1] : String(c[f]));

/** The number the row leads with: best catch, best margin or smallest radius. */
function headline(c: Candidate): string {
  const k = c.checks;
  if (c.mode === "budget") return `best ${k.best_pct?.toFixed(0)}%`;
  if (c.mode === "net") return `best net ${Math.round(k.best_net ?? 0).toLocaleString("en")}`;
  return `smallest ${Math.round(k.smallest_km ?? 0).toLocaleString("en")} km`;
}

const input = "w-full bg-ocean border border-edge px-2 py-1.5 text-[14px] text-chalk";

export default function Review() {
  const [all, setAll] = useState<Candidate[] | null>(null);
  const [counts, setCounts] = useState({ approved: 0, rejected: 0 });
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Partial<Record<Filter, string>>>({});
  const [current, setCurrent] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/dev/review")
      .then(async (r) => {
        const j = await r.json();
        if (!r.ok) throw new Error(j.error ?? r.statusText);
        setAll(j.candidates);
        setCounts({ approved: j.approved, rejected: j.rejected });
      })
      .catch((e) => setError(e.message));
  }, []);

  const options = useMemo(() => {
    const o = {} as Record<Filter, string[]>;
    for (const f of FILTERS) o[f] = [...new Set((all ?? []).map((c) => field(c, f)))].sort();
    return o;
  }, [all]);

  const list = useMemo(
    () => (all ?? []).filter((c) => FILTERS.every((f) => !filters[f] || field(c, f) === filters[f])),
    [all, filters],
  );

  const idx = list.findIndex((c) => c.key === current);
  const cand = idx >= 0 ? list[idx] : null;
  const next = () => setCurrent(list[idx + 1]?.key ?? null);

  async function send(d: Decision) {
    setError(null);
    const r = await fetch("/api/dev/review", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(d) });
    const j = await r.json();
    if (!r.ok) return setError(j.error ?? r.statusText);
    setCounts((c) => (d.status === "approved" ? { ...c, approved: c.approved + 1 } : { ...c, rejected: c.rejected + 1 }));
    next();
    setAll((a) => a!.filter((c) => c.key !== d.key));
  }

  if (cand) {
    // Review plays are stored under their own ID, so they never mix with real results.
    const puzzle = { ...cand, id: `review:${cand.key}`, country: "Review" } as Puzzle;
    return (
      <>
        <Game key={cand.key} puzzle={puzzle} daily={false} />
        <ReviewPanel
          key={`panel:${cand.key}`}
          cand={cand}
          position={`${idx + 1} of ${list.length}`}
          error={error}
          onSend={send}
          onSkip={next}
          onBack={() => setCurrent(null)}
        />
      </>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-4 py-8 text-chalk">
      <h1 className="font-display text-[28px] font-bold">Review candidates</h1>
      <p className="mt-1 text-[14px] text-mist">
        {all ? `${all.length.toLocaleString("en")} to review, ${list.length.toLocaleString("en")} shown.` : "Loading…"} {counts.approved} approved,{" "}
        {counts.rejected} rejected so far.
      </p>
      {error && <p className="mt-3 text-bad" role="alert">{error}</p>}

      <div className="mt-5 flex flex-wrap gap-3">
        {FILTERS.map((f) => (
          <label key={f} className="text-[13px] text-mist">
            <span className="block capitalize">{f}</span>
            <select className={input} value={filters[f] ?? ""} onChange={(e) => setFilters({ ...filters, [f]: e.target.value || undefined })}>
              <option value="">All</option>
              {options[f].map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
          </label>
        ))}
      </div>

      <ul className="mt-6 divide-y divide-edge border-y border-edge">
        {list.slice(0, 300).map((c) => (
          <li key={c.key}>
            <button onClick={() => setCurrent(c.key)} className="flex w-full items-center gap-4 py-3 text-left hover:bg-deep/40">
              <span className="w-14 text-[12px] capitalize text-mist">{c.difficulty}</span>
              <span className="min-w-0 flex-1">
                <span className="block text-[15px]">{c.prompt}</span>
                <span className="block text-[12px] text-mist">{c.key}</span>
              </span>
              <span className="w-36 text-right text-[13px] tabular-nums text-mist">
                {headline(c)}
                <span className="block">surprise {c.checks.surprise.toFixed(2)}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      {list.length > 300 && <p className="mt-3 text-[13px] text-mist">Showing the top 300. Use the filters to see the rest.</p>}
    </main>
  );
}

function ReviewPanel({
  cand,
  position,
  error,
  onSend,
  onSkip,
  onBack,
}: {
  cand: Candidate;
  position: string;
  error: string | null;
  onSend: (d: Decision) => void;
  onSkip: () => void;
  onBack: () => void;
}) {
  const [fact, setFact] = useState(cand.fact);
  const [prompt, setPrompt] = useState(cand.prompt);
  const [reason, setReason] = useState<string>(REJECT_REASONS[0]);
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(true);
  const k = cand.checks;

  return (
    <aside className="chamfer fixed right-3 top-28 z-50 w-80 max-w-[calc(100vw-1.5rem)] text-chalk">
      <div className="chamfer-in max-h-[calc(100dvh-15rem)] overflow-y-auto px-4 py-3 text-[13px]">
        <div className="flex items-center gap-2">
          <span className="font-display font-semibold text-amber">Review {position}</span>
          <button className="ghost-btn ml-auto" onClick={() => setOpen(!open)}>
            {open ? "Hide" : "Show"}
          </button>
        </div>
        {open && (
          <>
            <p className="mt-1 text-mist">
              <span className="capitalize">{cand.difficulty}</span>, surprise {k.surprise.toFixed(2)}, {headline(cand)}, naive {k.naive_median}
            </p>
            <p className="break-all text-[11px] text-mist">{cand.key}</p>

            <label className="mt-3 block text-mist">
              Prompt
              <textarea className={input} rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
            </label>
            <label className="mt-2 block text-mist">
              Fact
              <textarea className={input} rows={4} value={fact} onChange={(e) => setFact(e.target.value)} />
            </label>
            <button className="primary-btn mt-2 w-full" onClick={() => onSend({ key: cand.key, status: "approved", fact, prompt })}>
              Approve
            </button>

            <div className="mt-4 flex gap-2">
              <select className={input} value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reject reason">
                {REJECT_REASONS.map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
              <button className="outline-btn" onClick={() => onSend({ key: cand.key, status: "rejected", reason, note })}>
                Reject
              </button>
            </div>
            <input className={`${input} mt-2`} placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />

            {error && <p className="mt-2 text-bad" role="alert">{error}</p>}
            <div className="mt-3 flex justify-between">
              <button className="ghost-btn" onClick={onBack}>
                Back to list
              </button>
              <button className="ghost-btn" onClick={onSkip}>
                Skip
              </button>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
