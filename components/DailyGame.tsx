"use client";

import Game from "./Game";
import { todaysPuzzle, type Puzzle, type Schedule } from "@/lib/puzzles";

/** Rendered client-only (see app/page.tsx), so today's puzzle follows the player's local date. */
export default function DailyGame({ puzzles, schedule }: { puzzles: Puzzle[]; schedule: Schedule }) {
  return <Game puzzle={todaysPuzzle(puzzles, schedule)} daily />;
}
