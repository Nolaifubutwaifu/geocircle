import type { Metadata } from "next";
import Link from "next/link";
import countries from "@/public/data/countries-mode.json";
import { CountryList } from "@/components/ArchiveList";
import type { Puzzle } from "@/lib/puzzles";

export const metadata: Metadata = { title: "Halve a country · Geocircle" };

export default function Countries() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-8 sm:py-12">
      <nav className="flex gap-5 font-display text-[15px] font-semibold text-amber">
        <Link href="/" className="hover:underline">
          Today&apos;s puzzle
        </Link>
        <Link href="/archive" className="hover:underline">
          All puzzles
        </Link>
      </nav>
      <h1 className="mt-4 font-display text-[36px] font-bold leading-tight">Halve a country</h1>
      <p className="mt-2 max-w-prose text-[15px] text-mist">
        Pick a country and draw one circle that holds exactly half of its people. Some are easy. Try Egypt, Chile or Canada.
      </p>
      <CountryList puzzles={countries as Puzzle[]} />
    </main>
  );
}
