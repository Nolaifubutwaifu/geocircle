// Score formulas from GAME-SPEC.md section 1.
/** Hit-the-number modes: full marks at the target, 0 at 0% or double the target. */
export function scoreTarget(caughtPct: number, targetPct: number): number {
  return Math.max(0, Math.round(100 * (1 - Math.abs(caughtPct - targetPct) / targetPct)));
}

/** Budget modes: share of the best known catch, capped at 100. */
export function scoreBudget(caughtPct: number, bestPct: number): number {
  if (bestPct <= 0) return 0;
  return Math.min(100, Math.round((100 * caughtPct) / bestPct));
}
