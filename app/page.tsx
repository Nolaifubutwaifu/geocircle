import puzzles from "@/public/data/puzzles.json";
import DailyClient from "@/components/DailyClient";
import type { Puzzle } from "@/lib/puzzles";

export default function Home() {
  return <DailyClient puzzles={puzzles as Puzzle[]} />;
}
