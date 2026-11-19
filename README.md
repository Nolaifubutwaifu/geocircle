# Geocircle

Daily map puzzle: catch a share of a real-world stat (rice eaten, beer drunk, earthquakes) with one circle or one triangle. Spec: [GAME-SPEC.md](GAME-SPEC.md).

## Run it

```bash
pnpm install
pnpm dev
```

Open http://localhost:3000. `/archive` lists the 40 daily puzzles, `/countries` has a "halve the country" puzzle for 173 countries.

Puzzle modes: **target** (hit a %), **budget** (catch the most with a fixed circle or perimeter) and **net** (catch the most of one thing minus another, e.g. cows minus people). Any puzzle can be limited to some countries (`scope`).

## Data

Everything the game loads lives in `public/data/` and is built by one script:

```bash
pnpm data              # all steps
pnpm data stats puzzles  # just some steps: land, geo, population, stats, points, livestock, landcells, bycountry, apad, puzzles, countries, candidates, selftest
```

**Puzzle generator** ([PRD](PRD-puzzle-generator.md)): `pnpm data candidates` writes `scripts/pipeline/out/candidates.json` (git-ignored): every dataset × scope × mechanic × mode from the `DATASETS` registry, with every quality check, best shape, difficulty, prompt and fact. Shape searches are cached in `scripts/pipeline/.cache/`, so a rerun takes seconds; bump `GEN_VERSION` when the measuring code changes. It also writes `out/calibration.csv`: the same checks on the 40 handwritten puzzles, with empty columns for your own labels. `pnpm data selftest` runs its assert checks. To add a dataset: run its pipeline step, then add one `DATASETS` entry.

It reads raw downloads from `data-raw/` (not in git). Sources and download URLs:

| Folder | Source | Licence |
|---|---|---|
| `data-raw/ne/` | Natural Earth 1:10m land, minor islands, coastline, admin-0 countries, boundary lines, populated places | Public domain |
| `data-raw/kontur/` | Kontur Population 3 km (`kontur_population_20231101_r6.gpkg.gz`, HDX) | CC BY 4.0 |
| `data-raw/fao/` | FAOSTAT Food Balances, bulk normalized CSV | CC BY 4.0 |
| `data-raw/usgs/` | USGS earthquakes M4.5+, one CSV per year 2000 to 2025 (FDSN API) | Public domain |
| `data-raw/glw/` | FAO Gridded Livestock of the World 2015: cattle, sheep, pigs, chickens, goats (`5_*_2015_Da.tif`, Harvard Dataverse) | CC0 |
| `data-raw/apad/` | APAD industrial asset data (AWS Open Data, bucket `assetdata-igp`, ap-southeast-1): South Asia brick kilns, coal, cement, paper, waste sites; Africa power and waste sites | CC BY 4.0 / ODC-By 1.0 |
| `data-raw/overture/` | McDonald's from Overture Places: `uv run scripts/pipeline/overture_brand.py 2026-10-21.0 Q38076 data-raw/overture/mcdonalds-full.csv "mcdonald's%" ...` (resumable, one Parquet file at a time), then deduplicated within 150 m into `mcdonalds.csv` | CDLA Permissive 2.0 |

## Tests

```bash
pnpm test       # geometry, scoring, refine-vs-brute-force
pnpm test:e2e   # Playwright: circle, triangle, country, cows vs people, list pages
```

## Notes

- MapLibre is loaded from `public/maplibre/` (copied from `node_modules` by `predev`/`prebuild`) because the bundled copy hangs under Turbopack.
- Scoring runs in the browser and fetches fine cells with HTTP range requests, only along the shape's edge.
