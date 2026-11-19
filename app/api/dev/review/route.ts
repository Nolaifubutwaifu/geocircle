// Dev-only: candidates to review, and writing decisions to puzzles/curated.json. 404 in production.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { decide, type Candidate, type Curated, type Decision } from "@/lib/curated";

const ROOT = process.cwd();
const CANDIDATES = path.join(ROOT, "scripts/pipeline/out/candidates.json");
const CURATED = path.join(ROOT, "puzzles/curated.json");
const PUBLISHED = path.join(ROOT, "public/data/puzzles.json");

const dev = process.env.NODE_ENV === "development";
const gone = () => new Response("Not found", { status: 404 });
const readJson = async <T,>(file: string, fallback: T): Promise<T> =>
  JSON.parse(await readFile(file, "utf8").catch(() => JSON.stringify(fallback))) as T;

export async function GET() {
  if (!dev) return gone();
  const candidates = await readJson<Candidate[] | null>(CANDIDATES, null);
  if (!candidates) return Response.json({ error: "No candidates yet. Run: pnpm data candidates" }, { status: 409 });
  const curated = await readJson<Curated>(CURATED, {});
  const todo = candidates.filter((c) => c.checks.passed && !curated[c.key]).sort((a, b) => b.rank - a.rank);
  const decided = Object.values(curated);
  return Response.json({
    candidates: todo,
    approved: decided.filter((e) => e.status === "approved").length,
    rejected: decided.filter((e) => e.status === "rejected").length,
  });
}

export async function POST(req: Request) {
  if (!dev) return gone();
  const d = (await req.json()) as Decision;
  const candidates = await readJson<Candidate[]>(CANDIDATES, []);
  const curated = await readJson<Curated>(CURATED, {});
  const published = await readJson<{ id: number | string }[]>(PUBLISHED, []);
  try {
    const next = decide(curated, candidates.find((c) => c.key === d.key), d, published.map((p) => p.id));
    await mkdir(path.dirname(CURATED), { recursive: true });
    await writeFile(CURATED, JSON.stringify(next, null, 2) + "\n");
    return Response.json(next[d.key]);
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 400 });
  }
}
