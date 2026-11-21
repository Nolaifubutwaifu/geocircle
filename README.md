# Geocircle

Daily map puzzle: catch a share of a real-world stat (rice eaten, beer drunk, earthquakes) with one circle or one triangle. Spec: [GAME-SPEC.md](GAME-SPEC.md).

## Run it

```bash
pnpm install
pnpm dev
```

Open http://localhost:3000. `/archive` lists the daily puzzles up to today, `/countries` has a "halve the country" puzzle for 173 countries.

Puzzle modes: **target** (hit a %), **budget** (catch the most with a fixed circle or perimeter) and **net** (catch the most of one thing minus another, e.g. cows minus people). Any puzzle can be limited to some countries (`scope`).

## Data

Everything the game loads lives in `public/data/` and is built by one script:

```bash
pnpm data              # all steps
pnpm data stats puzzles  # just some steps: land, geo, population, stats, points, livestock, landcells, bycountry, apad, puzzles, publish, schedule, countries, candidates, duds, selftest
```

**Puzzle generator** ([PRD](PRD-puzzle-generator.md)): `pnpm data candidates` writes `scripts/pipeline/out/candidates.json` (git-ignored): every dataset × scope × mechanic × mode from the `DATASETS` registry, with every quality check, best shape, difficulty, prompt and fact. Shape searches are cached in `scripts/pipeline/.cache/`, so a rerun takes seconds; bump `GEN_VERSION` when the measuring code changes. It also writes `out/calibration.csv`: the same checks on the 40 handwritten puzzles, with empty columns for your own labels. `pnpm data duds` writes `out/duds.csv`: approved puzzles that look like duds (first guess under 20, target shape under 75 km, first guess 85+), read from the candidates cache, so run `candidates` first. It changes nothing. `pnpm data selftest` runs its assert checks. To add a dataset: run its pipeline step, then add one `DATASETS` entry.

**Reviewing:** with `pnpm dev` running, open [localhost:3000/dev/review](http://localhost:3000/dev/review). Filter the list, click a candidate, play it, then Approve (edit the fact or prompt first if needed), Reject with a reason, or Skip. Decisions go to `puzzles/curated.json` (commit it); an approved puzzle gets the next free ID. Then `pnpm data publish` adds the approved puzzles to `public/data/puzzles.json`. The page and its API return 404 in production builds.

**Scheduling:** `pnpm data schedule` fills `public/data/schedule.json` (local date to puzzle ID) up to 60 days ahead from approved puzzles nobody has had yet: easy Mon/Tue, medium Wed to Fri, hard Sat/Sun, no dataset twice within 7 days, no mechanic 3 days running, at least one size or "vs" puzzle a week. It never changes a date already in the file, so swap a day by editing it. 18 Nov to 27 Dec 2026 stay on puzzles 1 to 40. The home page shows today's scheduled puzzle, Next puzzle opens a random puzzle the player hasn't played. With `SHOW_FUTURE = false` in `lib/puzzles.ts`, the archive lists only days up to today and Next puzzle never opens a future daily; it is `true` for now, so every puzzle is open. `pnpm test` fails when fewer than 14 days are scheduled.

**Fun facts:** `puzzles/funfacts.json` holds sourced "Did you know?" facts per dataset. Set `"approved": true` on the ones you've checked, then `pnpm data publish`: each puzzle shows one approved fact from its dataset (rotating by ID), with a Source link in the reveal.

Weekly routine: `pnpm data candidates`, review at `/dev/review`, `pnpm data publish`, `pnpm data schedule`, commit, deploy.

It reads raw downloads from `data-raw/` (not in git). Sources and download URLs:

| Folder | Source | Licence |
|---|---|---|
| `data-raw/ne/` | Natural Earth 1:10m land, minor islands, coastline, admin-0 countries, boundary lines, populated places | Public domain |
| `data-raw/kontur/` | Kontur Population 3 km (`kontur_population_20231101_r6.gpkg.gz`, HDX) | CC BY 4.0 |
| `data-raw/fao/` | FAOSTAT Food Balances, bulk normalized CSV | CC BY 4.0 |
| `data-raw/usgs/` | USGS earthquakes M4.5+, one CSV per year 2000 to 2025 (FDSN API) | Public domain |
| `data-raw/glw/` | FAO Gridded Livestock of the World 2015: cattle, sheep, pigs, chickens, goats (`5_*_2015_Da.tif`, Harvard Dataverse: cattle doi:10.7910/DVN/LHBICE, sheep VZOYHM, pigs CIVCPB, chickens SXHLF3, goats YYG6ET) | CC0 on each record; FAO catalogue says CC BY 4.0, so credit the authors |
| `data-raw/apad/` | APAD industrial asset data (AWS Open Data, bucket `assetdata-igp`, ap-southeast-1): South Asia brick kilns, coal, cement, paper, waste sites; Africa power and waste sites | CC BY 4.0 / ODC-By 1.0 |
| `data-raw/pbdb/` | Dinosaur fossil finds, birds excluded: `curl -L "https://paleobiodb.org/data1.2/occs/list.csv?base_name=Dinosauria%5EAves&show=coords&limit=all" -o data-raw/pbdb/dinosauria.csv` | CC0 (stated in the API's `datainfo` header) |
| `data-raw/glottolog/` | Glottolog 5.4 `languages_and_dialects_geo.csv` (glottolog.org/meta/downloads) | CC BY 4.0 |
| `data-raw/hydrolakes/` | HydroLAKES v1.0 pour points, shapefile (`HydroLAKES_points_v10_shp.zip` from hydrosheds.org, unzipped) | CC BY 4.0 |
| `data-raw/overture/` | McDonald's from Overture Places: `uv run scripts/pipeline/overture_brand.py 2026-10-21.0 Q38076 data-raw/overture/mcdonalds-full.csv "mcdonald's%" ...` (resumable, one Parquet file at a time), then deduplicated within 150 m into `mcdonalds.csv` | CDLA Permissive 2.0 |

## Tests

```bash
pnpm test       # geometry, scoring, refine-vs-brute-force
pnpm test:e2e   # Playwright: circle, triangle, country, cows vs people, list pages
```

## Notes

- MapLibre is loaded from `public/maplibre/` (copied from `node_modules` by `predev`/`prebuild`) because the bundled copy hangs under Turbopack.
- Scoring runs in the browser and fetches fine cells with HTTP range requests, only along the shape's edge.
