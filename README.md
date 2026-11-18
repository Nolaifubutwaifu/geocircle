# Geocircle

Daily map puzzle: catch a share of a real-world stat (rice eaten, beer drunk, earthquakes) with one circle or one triangle. Spec: [GAME-SPEC.md](GAME-SPEC.md).

## Run it

```bash
pnpm install
pnpm dev
```

Open http://localhost:3000. `/archive` lists every puzzle.

## Data

Everything the game loads lives in `public/data/` and is built by one script:

```bash
pnpm data              # all steps
pnpm data stats puzzles  # just some steps: land, geo, population, stats, points, puzzles
```

It reads raw downloads from `data-raw/` (not in git). Sources and download URLs:

| Folder | Source | Licence |
|---|---|---|
| `data-raw/ne/` | Natural Earth 1:10m land, minor islands, coastline, admin-0 countries, boundary lines, populated places | Public domain |
| `data-raw/kontur/` | Kontur Population 3 km (`kontur_population_20231101_r6.gpkg.gz`, HDX) | CC BY 4.0 |
| `data-raw/fao/` | FAOSTAT Food Balances, bulk normalized CSV | CC BY 4.0 |
| `data-raw/usgs/` | USGS earthquakes M4.5+, one CSV per year 2000 to 2025 (FDSN API) | Public domain |
| `data-raw/overture/` | McDonald's from Overture Places: `uv run scripts/pipeline/overture_brand.py 2026-10-21.0 Q38076 data-raw/overture/mcdonalds.csv` | CDLA Permissive 2.0 |

## Tests

```bash
pnpm test       # geometry, scoring, refine-vs-brute-force
pnpm test:e2e   # Playwright: circle, triangle, archive
```

## Notes

- MapLibre is loaded from `public/maplibre/` (copied from `node_modules` by `predev`/`prebuild`) because the bundled copy hangs under Turbopack.
- Scoring runs in the browser and fetches fine cells with HTTP range requests, only along the shape's edge.
