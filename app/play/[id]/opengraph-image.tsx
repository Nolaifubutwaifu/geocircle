import puzzles from "@/public/data/puzzles.json";
import { OG_SIZE, ogPng } from "@/lib/brand";
import { goalText, puzzleLabel, type Puzzle } from "@/lib/puzzles";

export const alt = "A Geocircle puzzle";
export const size = OG_SIZE;
export const contentType = "image/png";

export default async function Image({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const p = (puzzles as Puzzle[]).find((x) => String(x.id) === id)!;
  return ogPng(`${puzzleLabel(p)} · ${goalText(p)}`, p.prompt);
}
