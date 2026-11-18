"use client";

import Game from "./Game";
import { todaysPuzzle, type Puzzle } from "@/lib/puzzles";

/** Rendered client-only (see app/page.tsx), so today's puzzle follows the player's local date. */
export default function DailyGame({ puzzles }: { puzzles: Puzzle[] }) {
  return <Game puzzle={todaysPuzzle(puzzles)} daily />;
}
