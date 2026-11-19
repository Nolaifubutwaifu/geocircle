import { notFound } from "next/navigation";
import puzzles from "@/public/data/puzzles.json";
import GameClient from "@/components/GameClient";
import { puzzleLabel, type Puzzle } from "@/lib/puzzles";

export function generateStaticParams() {
  return (puzzles as Puzzle[]).map((p) => ({ id: String(p.id) }));
}

export async function generateMetadata({ params }: PageProps<"/play/[id]">) {
  const { id } = await params;
  const p = (puzzles as Puzzle[]).find((x) => String(x.id) === id);
  return p ? { title: `Geocircle ${puzzleLabel(p)}`, description: p.prompt } : {};
}

export default async function PlayPage({ params }: PageProps<"/play/[id]">) {
  const { id } = await params;
  const puzzle = (puzzles as Puzzle[]).find((p) => String(p.id) === id);
  if (!puzzle) notFound();
  return <GameClient key={String(puzzle.id)} puzzle={puzzle} daily={false} />;
}
