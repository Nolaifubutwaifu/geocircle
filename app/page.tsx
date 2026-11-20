import puzzles from "@/public/data/puzzles.json";
import schedule from "@/public/data/schedule.json";
import DailyClient from "@/components/DailyClient";
import type { Puzzle } from "@/lib/puzzles";

// Only scheduled puzzles go to the client (the full list is ~600 KB); it picks today's by the player's local date.
const scheduled = new Set(Object.values(schedule));
const daily = (puzzles as Puzzle[]).filter((p) => scheduled.has(p.id as number));

export default function Home() {
  return <DailyClient puzzles={daily} schedule={schedule} />;
}
