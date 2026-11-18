import { notFound } from "next/navigation";
import puzzles from "@/public/data/puzzles.json";
import GameClient from "@/components/GameClient";
import type { Puzzle } from "@/lib/puzzles";

export function generateStaticParams() {
  return (puzzles as Puzzle[]).map((p) => ({ id: String(p.id) }));
}

export default async function PlayPage({ params }: PageProps<"/play/[id]">) {
  const { id } = await params;
  const puzzle = (puzzles as Puzzle[]).find((p) => String(p.id) === id);
  if (!puzzle) notFound();
  return <GameClient puzzle={puzzle} daily={false} />;
}
