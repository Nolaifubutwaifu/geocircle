// Player stats from the results saved on this device.
import { localDate, type Schedule } from "./puzzles";
import type { Result } from "./storage";

export type Stats = { played: number; avg: number; streak: number; best: number };

/**
 * Streak = consecutive days on which the player played that day's daily puzzle on that day.
 * The current streak survives until the end of today, so it counts up to yesterday if today isn't played yet.
 */
export function playerStats(results: Record<string, Result>, schedule: Schedule, now = new Date()): Stats {
  const today = localDate(now);
  const daily = Object.entries(results).filter(([id]) => /^\d+$/.test(id)).map(([, r]) => r);
  const onTheDay = (d: string) => {
    const r = results[String(schedule[d])];
    return !!r && localDate(new Date(r.playedAt)) === d;
  };
  let run = 0;
  let best = 0;
  let upToYesterday = 0;
  // Schedule dates are consecutive from launch day, so walking them in order walks the calendar.
  for (const d of Object.keys(schedule).filter((d) => d <= today).sort()) {
    if (d === today) upToYesterday = run;
    run = onTheDay(d) ? run + 1 : 0;
    best = Math.max(best, run);
  }
  const streak = schedule[today] !== undefined && onTheDay(today) ? run : upToYesterday;
  const avg = daily.length ? Math.round(daily.reduce((s, r) => s + r.score, 0) / daily.length) : 0;
  return { played: daily.length, avg, streak, best };
}

/** "5h 12m" until the next local midnight, when the next daily puzzle unlocks. */
export function untilMidnight(now = new Date()): string {
  const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  const mins = Math.max(1, Math.ceil((next.getTime() - now.getTime()) / 60_000));
  return mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;
}
