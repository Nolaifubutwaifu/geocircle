import { notFound } from "next/navigation";
import countries from "@/public/data/countries-mode.json";
import GameClient from "@/components/GameClient";
import type { Puzzle } from "@/lib/puzzles";

const all = countries as Puzzle[];

export function generateStaticParams() {
  return all.map((p) => ({ code: p.code!.toLowerCase() }));
}

export default async function CountryPage({ params }: PageProps<"/country/[code]">) {
  const { code } = await params;
  const puzzle = all.find((p) => p.code!.toLowerCase() === code.toLowerCase());
  if (!puzzle) notFound();
  return <GameClient puzzle={puzzle} daily={false} />;
}
