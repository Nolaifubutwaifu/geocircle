import type { Metadata } from "next";
import Link from "next/link";
import puzzles from "@/public/data/puzzles.json";
import ArchiveList from "@/components/ArchiveList";
import type { Puzzle } from "@/lib/puzzles";

export const metadata: Metadata = { title: "All puzzles · Geocircle" };

export default function Archive() {
  return (
    <main className="mx-auto max-w-2xl px-4 py-8 sm:py-12">
      <Link href="/" className="font-display text-[15px] font-semibold text-amber hover:underline">
        Today&apos;s puzzle
      </Link>
      <h1 className="mt-4 font-display text-[36px] font-bold leading-tight">All puzzles</h1>
      <p className="mt-2 max-w-prose text-[15px] text-mist">Every puzzle so far. Your scores stay on this device.</p>
      <ArchiveList puzzles={puzzles as Puzzle[]} />
    </main>
  );
}
