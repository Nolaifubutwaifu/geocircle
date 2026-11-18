# Geocircle: game spec v0.1

Daily map game. Each day has one odd statistic ("rice eaten", "sheep", "McDonald's") and one shape to draw. You guess where the stuff is by catching it in a circle or a triangle. The score comes from real data.

Two mechanics only: **Circle** and **Triangle**. Every daily puzzle uses one of them with a new dataset. New days need new data, not new code.

---

## 1. Mechanic A: Circle

### Interaction
1. **Tap 1** drops the centre pin.
2. **Drag outward** from the centre. The circle grows with the pointer. A label on the edge shows the radius live, e.g. `1,240 km`.
3. **Release** locks it. Two handles stay editable until submit:
   - centre pin: drag to move the whole circle
   - edge handle: drag to resize
4. **Submit** button. No undo after submit.

### Hard rules
| Rule | Value |
|---|---|
| Min radius | 10 km |
| Max radius | 5,000 km |
| Circle geometry | Geodesic (true distance on the globe), drawn as a 128-point polygon |
| Live feedback before submit | Radius only. Never the captured %. |
| Mobile | Same flow. Tap, then drag with one finger. Pinch zoom stays available before tap 1 and after release. |

Geodesic circles look stretched near the poles on a flat map. Show a one-line hint the first time: "Circles look stretched near the poles because the map is flat. The distance is real."

### Modes
**A1. Hit the number**
- Prompt: "Draw a circle that holds exactly **20%** of the world's rice eaten."
- Score: `max(0, round(100 × (1 − |caught − target| / target)))`
  - target 20%, caught 25% → 75
  - target 20%, caught 0% or 40%+ → 0
- Secondary stat shown after submit: your radius. ("You did it with a 1,240 km circle.")

**A2. Fixed size, catch the most**
- Prompt: "You get a **1,000 km** circle. Catch as many of the world's sheep as you can."
- Radius is fixed. Player only places the centre (tap, then drag to adjust).
- Score: `round(100 × caught / best_possible)`
- `best_possible` is precomputed offline by testing every cell centre as a circle centre. Reveal shows the best spot.

---

## 2. Mechanic B: Triangle (3 pins)

### Interaction
1. **Tap 1** drops pin A.
2. **Tap 2** drops pin B. A line A to B appears.
3. **Tap 3** drops pin C. The triangle fills.
4. All three pins stay draggable until submit. Side lengths and perimeter show live.
5. **Submit.**

### Hard rules
| Rule | Value |
|---|---|
| Edges | Great-circle lines (curved on the flat map) |
| Max side length | 8,000 km (keeps "inside" unambiguous) |
| Min side length | 10 km |
| Invalid triangle | Edges turn red, submit disabled, one-line reason shown |
| Live feedback before submit | Side lengths and perimeter only |

### Modes
**B1. Hit the number**
- Prompt: "Put down 3 pins. Your triangle must hold exactly **50%** of the world's coffee drunk."
- Score: same formula as A1.

**B2. Perimeter budget, catch the most**
- Prompt: "Your triangle can be at most **6,000 km** around. Catch as many cows as you can."
- Pins that would break the budget turn the perimeter label red and disable submit.
- Score: `round(100 × caught / best_known)`
- `best_known` comes from an offline search (random restarts plus hill climbing). It is not a proven optimum, so label it "best we found". If a player beats it, cap the score at 100 and store their triangle as the new best.

---

## 3. Shared engine

### Data format: H3 cells, fine detail only where needed
- Population-based stats use **H3 resolution 5** cells (edge about 8.5 km). Point stats (earthquakes, McDonald's) use **resolution 7** (edge about 1.2 km).
- Each dataset ships as a total per resolution 3 cell (about 60 km) plus a binary file of fine cells grouped by their r3 parent. The browser fetches fine cells with HTTP range requests.
- Food stats share one population file. `weight = population × kg per person in that country` is computed in the browser.
- Formats are documented at the top of `scripts/pipeline/build.py`.

### Inside test (client side, after submit)
- r3 cells fully inside the shape add their total. r3 cells fully outside are skipped. Only r3 cells the shape edge crosses load their fine cells.
- Circle: a fine cell counts if its centre is within the radius (haversine). Triangle: if its centre is on the same side of all three great circles.
- A unit test checks that this refined sum equals a brute-force sum for 200 random shapes.

Shortcut: the cell files are public, so a determined player could work out the answer from the network tab. Fine for v1. Move scoring to a server route if a leaderboard is added.

### How "rice eaten" works when there is no rice grid
Most fun stats only exist per country. Spread them with population:

```
cell_weight = population_in_cell × per_capita_value[country_of_cell]
```

Example: rice. Use FAOSTAT "Rice and products", food supply in kg per person per year, per country. Asia then dominates far more than raw population would suggest, which is where the surprise comes from. Country lookup per cell from Natural Earth admin-0 boundaries (public domain).

### Puzzle record

```json
{
  "id": 41,
  "date": "2026-12-01",
  "stat": "rice eaten",
  "prompt": "Draw a circle that holds exactly 20% of the world's rice eaten.",
  "mechanic": "circle",
  "mode": "target",
  "target_pct": 20,
  "radius_km": null,
  "perimeter_km": null,
  "best": { "pct": null, "shape": null },
  "cells_file": "/data/rice-eaten-2023.json",
  "credit": "FAO FAOSTAT Food Balances (CC BY 4.0); Kontur Population (CC BY 4.0)"
}
```

- `mode`: `"target"` (A1, B1) or `"budget"` (A2, B2)
- `best.shape`: `{ "center": [lng, lat], "radius_km": 1000 }` or `{ "pins": [[lng, lat], [lng, lat], [lng, lat]] }`
- `cells_file` format: `[["8428309ffffffff", 412], ...]`

### Reveal screen
1. Heatmap of the dataset fades in under your shape.
2. Big number: "You caught **23.4%**" (target modes) or "**87%** of the best possible" (budget modes).
3. Best shape drawn in a second colour (budget modes).
4. One fun fact line, written per puzzle.
5. Share text: `Geocircle #41 ⭕ 87/100`

---

## 4. Datasets

Licence matters because a Premium tier makes this commercial use. Anything non-commercial (NC) is out.

**Licence column:** ✅ = confirmed commercial use allowed on the source page or catalogue. ⚠️ = conflicting or missing licence info, open the dataset page and check before shipping. Checked 18 Nov 2026.

### 4a. On AWS Open Data (free, no AWS account needed to download)

| # | Fun prompt | Dataset | Type | Licence | Notes |
|---|---|---|---|---|---|
| 1 | Base population layer | Meta / CIESIN High Resolution Population Density Maps | 30 m grid | ✅ CC BY 4.0 | Alternative to Kontur. Credit Maxar imagery. |
| 2 | "Catch the most lightning strikes in the Americas **this week**" | NOAA GOES GLM lightning (GOES-East/West) | Points, new file every ~20 s | ✅ NOAA data, no copyright | Covers the Americas and nearby oceans only. Lets you run fresh weekly puzzles. |
| 3 | "Catch 10% of the world's **McDonald's**" / Starbucks / IKEA / KFC / pubs / churches | Overture Maps Places | Points with brand field | ✅ CDLA Permissive 2.0 / Apache 2.0 | No OSM share-alike. Coverage varies by country, so phrase it as "mapped McDonald's". |
| 4 | "Catch 25% of the world's **mangroves**" / snow & ice / moss & lichen / cropland | ESA WorldCover 10 m | Land cover grid | ✅ CC BY 4.0 | Big files (10 m). Aggregate once, keep only the H3 table. |
| 5 | "Catch the most reported **penguin** / kangaroo / shark / flamingo sightings" | GBIF species occurrences (monthly Parquet snapshot) | Points | ⚠️ Per record: CC0, CC BY or CC BY-NC | Filter out the CC BY-NC records. Sightings follow where birders live, so say "reported sightings", which is part of the joke. |

### 4b. Other sources

| # | Fun prompt | Dataset | Type | Licence | Notes |
|---|---|---|---|---|---|
| 6 | Base population layer (recommended) | Kontur Population, 22 km H3 | H3 res 4 | ✅ CC BY | Already in the game's grid. Also 3 km and 400 m versions. |
| 7 | "Split the world's **rice / beer / wine / coffee / chocolate / potatoes / bananas / cheese / fish / sugar / pork** eaten" | FAOSTAT Food Balances (per person per year, by country) | Per country × population | ✅ CC BY 4.0 | One join gives 12+ puzzles. Cheapest start. Cite as "FAO". |
| 8 | "Catch 25% of the world's **coffee grown**" / cocoa / rice / bananas | SPAM 2020 (IFPRI MapSPAM), 46 crops | 10 km grid | ✅ CC BY 4.0 | Pair with #7: coffee grown on Monday, coffee drunk on Tuesday. Download needs a short guestbook form. |
| 9 | "1,000 km circle: catch the most **sheep**" / cows / chickens / pigs / goats / ducks / horses / buffalo | FAO Gridded Livestock of the World (GLW4, 2015 and 2020) | 10 km grid | ⚠️ Dataverse says CC BY 4.0, one record CC0, FAO parent page says CC BY-NC-SA | Check the licence on each species file. NZ/Australia sheep puzzle lives here. |
| 10 | "Catch 10% of **all lightning ever recorded**" | NASA LIS/OTD lightning climatology, 0.5° | Grid | ✅ NASA open data | Global (unlike #2). Peak is eastern Congo, which few people guess. |
| 11 | "Catch the most **light pollution**" | VIIRS Nighttime Lights annual (Colorado School of Mines EOG) | 500 m grid | ⚠️ Listed as public domain in some catalogues, CC BY in others | Check eogdata.mines.edu. |
| 12 | "Catch the most **wildfires this month**" | NASA FIRMS active fires | Points, near real time | ✅ Open, citation and disclaimer required | Fresh data each week. |
| 13 | "Catch the most **hurricane** track since 1980" | NOAA IBTrACS v4 | Lines (sample every 3 h as points) | ✅ NOAA, no copyright | Cite the dataset DOI. |
| 14 | "Catch the most **earthquakes** M4.5+ since 2000" | USGS earthquake catalogue | Points | ✅ US government, public domain | Ring of Fire makes triangles interesting. |
| 15 | "Catch the most **meteorites found**" | NASA / Meteoritical Society Meteorite Landings (~45k) | Points | ⚠️ Portal says "licence not specified" | Antarctica and the Sahara dominate, a good surprise. |
| 16 | "Catch 20% of the world's **nuclear** / coal / wind power capacity" | WRI Global Power Plant Database v1.3 | Points with MW | ✅ CC BY 4.0 | Not updated since 2021. Say "as of 2021". |
| 17 | "Catch the most **metal bands**" (by home town) | MusicBrainz | Points via artist area | ⚠️ Core data is CC0, but genre tags may fall under the NC part | Check which part tags belong to before using. |

### 4c. Avoid (non-commercial licence)
- **Ookla Speedtest** (on AWS): CC BY-NC-SA 4.0
- **Global Fishing Watch** fishing effort: non-commercial
- **WHO alcohol data**: older GHO listings say CC BY-NC-SA. Use FAOSTAT beer and wine instead.
- **Airports, volcanoes, animals as raw counts**: not a licence issue, but GeoSlice's weekly challenge already uses them.

---

## 5. Build order
1. **Pipeline v1**: Kontur 22 km + FAOSTAT + Natural Earth. One Python script outputs one H3 JSON file per food stat. Gives 12+ puzzles.
2. **Circle mechanic**, both modes, on those files.
3. **Triangle mechanic**, both modes.
4. **Add gridded sources** (SPAM, LIS/OTD, WorldCover) with an aggregate-to-H3 script.
5. **Add point sources** (Overture brands, earthquakes, meteorites) with a count-per-cell script.
6. **Fresh weekly puzzles** from GLM lightning and FIRMS fires.

## 6. Open decisions
- Map projection: plain Web Mercator (familiar, distorts circles) or an equal-area globe view (honest, less familiar). Recommendation: Mercator with geodesic shapes drawn on it.
- Mode schedule: fixed weekdays per mode, or chosen per puzzle.
- Leaderboard (smallest circle that hits the target) needs server-side scoring. Not in v1.

## Sources
- [Registry of Open Data on AWS](https://registry.opendata.aws/)
- [Meta / CIESIN population on AWS](https://registry.opendata.aws/dataforgood-fb-hrsl/)
- [NOAA GOES GLM lightning, data.gov record](https://catalog.data.gov/dataset/noaa-goes-r-series-geostationary-lightning-mapper-glm-level-2-lightning-detection-events-g)
- [Overture Places guide (licence)](https://docs.overturemaps.org/guides/places) and [Overture on AWS](https://registry.opendata.aws/overture/)
- [ESA WorldCover on AWS](https://registry.opendata.aws/esa-worldcover-vito)
- [GBIF on AWS](https://registry.opendata.aws/gbif/)
- [Kontur Population on HDX](https://data.humdata.org/dataset/kontur-population-dataset)
- [FAOSTAT Food Balances catalogue entry](https://data.fao.org/catalog/dataset/2f264bb6-1238-459a-bf8b-0e2d0a16804a) and [FAO database terms](https://www.fao.org/contact-us/terms/db-terms-of-use)
- [SPAM 2020 FAO catalogue entry (CC BY 4.0, doi:10.7910/DVN/SWPENT)](https://data.fao.org/catalog/iso/3757ea29-7c21-419e-a9d0-46e0b268049e)
- [GLW4 on Harvard Dataverse](https://dataverse.harvard.edu/dataverse/glw) and [FAO GLW catalogue (conflicting licence)](https://data.apps.fao.org/catalog/dataset/glw)
- [LIS/OTD lightning climatology](https://catalog.data.gov/dataset/lis-otd-0-5-degree-high-resolution-full-climatology-hrfc-v2-3-2015)
- [VIIRS night lights (licence notes)](https://gee-community-catalog.org/projects/eog_viirs_ntl/)
- [NASA FIRMS](https://developers.google.com/earth-engine/datasets/catalog/FIRMS)
- [IBTrACS v4](https://www.ncei.noaa.gov/products/international-best-track-archive)
- [Meteorite Landings](https://data.nasa.gov/dataset/meteorite-landings)
- [WRI Global Power Plant Database](https://github.com/wri/global-power-plant-database)
- [Ookla on AWS (NC licence)](https://registry.opendata.aws/speedtest-global-performance/)
- [Global Fishing Watch licence](https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits)
- [WHO dataset terms](https://data.who.int/redirect-pages/footer/learn-more/terms-and-conditions)
