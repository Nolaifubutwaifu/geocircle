# PRD #1: Puzzle generator

Written 19 Nov 2026. Part of Phase 1 in [ROADMAP.md](ROADMAP.md). Game rules live in [GAME-SPEC.md](GAME-SPEC.md).

## 1. Problem

- The game has **40 handwritten puzzles** in `PUZZLES` (`scripts/pipeline/build.py`). The daily puzzle is `puzzles[dayIndex % 40]`, so on **28 Dec 2026** (day 40) the daily loops back to puzzle 1. That is the hard deadline.
- Each puzzle is manual work: pick a dataset, scope, target, write the prompt, play it to see if it's fun. At a few hours a week that is about 5 puzzles a week. The roadmap needs 300 before premium.
- Nothing stops a boring or broken puzzle from shipping, for example "half of X" where one city holds 60% of X.
- Difficulty is unknown, so there is no way to schedule easy Mondays and hard Sundays.

## 2. Goal

One script turns the datasets into ranked, quality-checked puzzle candidates. Max approves them by playing them on a local review page. A schedule fills the daily slot automatically, 60 days ahead.

**Done when** (Phase 1 exit): 150+ approved puzzles, 60 days scheduled ahead, no daily repeats.

## 3. Users

| User | What they need |
|---|---|
| Curator (Max), about 1 hour a week | Play the best candidates fast, approve or reject, fix the fun fact. Never edit Python to add a puzzle. |
| Player | A different, working puzzle every day. Easier early in the week, harder at the weekend. Never sees the generator. |

## 4. Flow

```
DATASETS registry
  -> enumerate candidates (dataset x scope x mechanic x mode x parameter)
  -> cheap checks (index files only)
  -> expensive checks (best shape, difficulty)
  -> candidates.json
  -> /dev/review: play, approve or reject
  -> curated.json (in git)
  -> publish: puzzles.json + schedule.json
  -> the game
```

## 5. Requirements

### 5.1 Dataset registry

Dataset details move out of each puzzle into one list, `DATASETS`, in the pipeline. One entry per dataset:

| Field | Example | Notes |
|---|---|---|
| `key` | `coffee` | Unique. |
| `cells` | `population` | Fine-cell file name, as today. |
| `food` | `coffee` | Per-person multiplier file, or `null`. |
| `stat` | `coffee drunk` | Noun phrase used in prompts. |
| `noun` | `mass` or `count` | "as much coffee drunk" vs "as many earthquakes". |
| `credit` | `FOOD_CREDIT` | Existing credit constants. |
| `licence_ok` | `true` | `true` only if GAME-SPEC §4 marks it ✅ or Max has checked the source page. The generator skips `false`. |
| `baseline` | `false` | `true` for `population` and `land`: exempt from the surprise check. |
| `coverage` | `null` | Optional list of allowed scopes. APAD datasets only cover their region. `null` = any scope. |
| `vs` | `null` | For net mode: the other dataset key and label. v1 only allows `population` / "people". |

Adding a new dataset = run its pipeline step + add one registry entry. No other code.

### 5.2 Candidate enumeration

Every combination of dataset × scope × mechanic × mode × parameter.

**Scopes**
- World.
- The 6 inhabited continents (Natural Earth `CONTINENT`).
- Named regions already in use (Indo-Gangetic Plain).
- Single countries where the dataset is meaningful: the country holds **≥ 2%** of the world total and **≥ 200** fine cells with weight above 0.

**Modes and parameters**

| Mode | Parameter | Rule |
|---|---|---|
| Target, circle and triangle | `target_pct` in {10, 20, 25, 30, 50} | 50 only for country and region scopes ("half of Brazil's ..."). |
| Budget, circle | radius from {100, 150, 200, 250, 300, 400, 500, 750, 1,000, 1,500, 2,000} km | Keep the one radius whose best catch is closest to 35%. |
| Budget, triangle | perimeter from {1,000, 1,500, 2,000, 3,000, 4,000, 5,000, 6,000, 8,000, 10,000} km | The ladder value nearest 8 × the chosen circle radius (an equilateral triangle with that perimeter has about the circle's area). One triangle search per candidate, not one per ladder step. |
| Net, circle only | radius, same ladder | Keep the radius whose best net is highest relative to the scope total of the stat. |

**Candidate key**, deterministic: `{dataset}|{scope}|{mechanic}|{mode}|{param}`, for example `coffee|BRA|triangle|target|50`. Scope codes: `world`, continent name, region name, ISO A3 for countries. The key identifies a candidate across runs, so a rejection stays rejected.

Expect a few thousand raw candidates. Most get filtered out.

### 5.3 Quality checks

All computed in Python from the same files the game loads. Numbers are starting values, tuned in §5.5.

| Check | Rule | Why |
|---|---|---|
| Licence | dataset `licence_ok` is `true` | Premium makes this commercial use. |
| Enough data | scope has ≥ 200 fine cells with weight above 0 | Avoids pinpoint puzzles. |
| No single hotspot | largest fine cell holds < 20% of the scope total, and in target mode < half the target | Otherwise the answer is "tap the city". |
| Reachable (target) | best circle at 5,000 km, or best triangle at max size, catches ≥ target + 10 points | The target can be hit with room to spare. |
| Not a pinpoint (target) | smallest circle holding the target has a radius ≥ 25 km | Below that a finger on a phone can't do it. |
| Budget range | best catch between 20% and 60% | 100% is trivial, 5% is fiddly. |
| Net positive | best net > 0 | Otherwise every answer scores 0. |
| Surprise | ≥ 0.25 unless the dataset is `baseline` | Must differ from "where people live". |
| Not a duplicate | no approved puzzle (legacy included) with the same dataset, scope, mechanic and mode | Variety. |

**Surprise score.** Total variation distance between the dataset and population inside the scope, at r3 (about 60 km cells):

```
surprise = 0.5 × Σ |a_i − p_i|
a_i = dataset share of the scope total in r3 cell i
p_i = population share of the scope total in r3 cell i
```

0 = laid out exactly like people, 1 = no overlap at all. It only needs the `.index.json` files, so it is cheap. Food stats are population × kg per person, so their surprise comes only from differences between countries.

**Rank** for the review page: surprise, then datasets with fewer approved puzzles first.

### 5.4 Difficulty estimate

Simulate a naive player who assumes the stat follows people. 400 shapes per candidate:

| Mode | Simulated shape |
|---|---|
| Target, circle | Centre: a random population cell in scope, picked with probability ∝ population. Radius: the radius that holds `target_pct` of the scope's *population* at that centre, × lognormal noise (σ = 0.4). |
| Target, triangle | Equilateral triangle with the same area as that circle, random rotation. |
| Budget | Centre as above, fixed size. |
| Net | Centre: a random cell picked ∝ the stat itself, moved up to 500 km in a random direction. |

Score each shape with the game's formulas (`scoreTarget`, `scoreBudget`, `scoreNet`). r3 aggregates are fine here: it's an estimate.

| Median naive score | Label |
|---|---|
| ≥ 55 | Easy |
| 30 to 54 | Medium |
| < 30 | Hard |

The label is shown to players on the puzzle card (Phase 1: "difficulty labels"). Once analytics exist and a puzzle has 30+ plays, the real median score replaces the simulated one (field `difficulty_source`: `sim` or `players`). That part is out of scope here.

### 5.5 Calibration on the 40 existing puzzles

Before trusting any threshold:
1. Max labels each of the 40 current puzzles in a sheet: **good / boring / too hard / broken**, plus Easy / Medium / Hard by feel.
2. Run all checks and the difficulty estimate on them.
3. Tune the thresholds until the checks agree with **≥ 80%** of the labels (good ones pass, boring and broken ones fail) and the difficulty label is within one step of Max's for **≥ 80%**.
4. Put the final table in the pull request.

### 5.6 Prompt, fact, credit, view

**Prompt templates**, matching today's wording:

| Mechanic, mode | Template |
|---|---|
| Circle, target | Draw a circle that holds exactly {pct} of {whose} {stat}. |
| Triangle, target | Put down 3 pins. Your triangle must hold exactly {pct} of {whose} {stat}. |
| Circle, budget | You get a {r} km circle. Catch as {much/many} of {whose} {stat} as you can. |
| Triangle, budget | Your triangle can be at most {p} km around. Catch as {much/many} of {whose} {stat} as you can. |
| Circle, net | {Stat} vs people{ in scope}. Your {r} km circle should catch the most {stat} beyond the number of people. |

- `{pct}`: "half" for 50, otherwise "25%".
- `{whose}`: "the world's", "Europe's", "Brazil's", "the USA's", "the UK's" (existing replacements).
- Land uses "covers" instead of "holds" (existing rule).
- Numbers use thousands separators: "1,500 km".
- Every generated prompt: ends with ".", no double spaces, no "the the", **≤ 140 characters**.

**Fact:** generated by the existing `make_fact`. Max can edit it on the review page before approving. His text wins.

**Credit:** from the registry. **View:** `auto_view` (existing).

### 5.7 Review page

- Local only: `/dev/review`. Exists in `pnpm dev`, returns 404 in production builds.
- Lists candidates that passed all checks and are neither approved nor rejected, sorted by rank. Filters: dataset, scope, mechanic, mode, difficulty.
- Each row shows: prompt, difficulty, surprise, best catch or smallest radius.
- Click a row to play it in the normal `Game` component, exactly like a player. The reveal shows the heatmap, best shape and generated fact.
- After playing:
  - **Approve**: editable fact, optional prompt edit.
  - **Reject**: reason from boring / too hard / broken / wrong data, optional note.
  - **Skip**: no record, shows up again next time.
- Approve and Reject write to `puzzles/curated.json` through a dev-only API route.
- Target speed: **≤ 3 minutes per candidate** including the play, so a week of puzzles takes ≤ 30 minutes.

### 5.8 IDs and published files

- Puzzles 1 to 40 keep their IDs forever. Share links, `/play/{id}` URLs and saved results in localStorage all use the ID.
- An approved candidate gets the **next free integer ID** at approval. IDs are never reused or renumbered.
- The 40 handwritten puzzles stay in `PUZZLES` unchanged ("legacy"). The generator records their keys so it doesn't produce duplicates.
- `public/data/puzzles.json` = legacy + approved, sorted by ID. Same `Puzzle` type as today plus two optional fields: `key` and `difficulty` (`"easy" | "medium" | "hard"`).
- Players keep seeing the puzzle ID as the number ("No. 87"). No separate day number.

### 5.9 Schedule

**File:** `public/data/schedule.json`, local date to puzzle ID:

```json
{ "2026-11-18": 1, "2026-11-19": 2, "2026-12-27": 40, "2026-12-28": 57 }
```

- 18 Nov to 27 Dec 2026 are fixed to IDs 1 to 40, so nobody's past days change.
- `pnpm data schedule` fills empty dates up to **60 days ahead** from approved, never-scheduled puzzles. Rules, highest priority first:
  1. Weekday difficulty: Mon Easy, Tue Easy, Wed Medium, Thu Medium, Fri Medium, Sat Hard, Sun Hard. If none are left at that level, take the nearest level.
  2. Same dataset not within 7 days.
  3. Same mechanic not 3 days in a row.
  4. At least one budget or net puzzle per 7 days.
  5. Each puzzle is scheduled at most once.
- The script never changes a date that is already in the file. Max swaps a day by editing the file.
- The script prints how many days are scheduled ahead.

**Client changes**
- `todaysPuzzle` looks up today's local date in the schedule. If the date is missing, it falls back to today's behaviour (`dayIndex % count`), so the site never breaks.
- `/archive` lists only puzzles scheduled on or before today, newest first. Today it lists all 40, which would spoil future dailies once there are hundreds. Unscheduled puzzles stay reachable through "Random puzzle".

### 5.10 Commands and runtime

| Command | Output |
|---|---|
| `pnpm data candidates` | `scripts/pipeline/out/candidates.json` (git-ignored) |
| `pnpm data publish` | `public/data/puzzles.json` from legacy + `puzzles/curated.json` |
| `pnpm data schedule` | `public/data/schedule.json` |

- Order inside `candidates`: cheap checks (index files only) first, then circle searches, then triangle searches only for survivors.
- Results are cached by candidate key plus a hash of the input files.
- Full run **≤ 60 minutes** on Max's Mac. Rerun with no data changes **≤ 2 minutes**.

### 5.11 Tests

- Candidate keys are stable across runs.
- Prompt templates: every mechanic × mode × scope type, count and mass nouns, the "half" case, the 140-character limit.
- Schedule rules on a fake pool: no dataset within 7 days, no mechanic 3 days running, weekday difficulty, fixed days 1 to 40 untouched, existing dates never changed.
- Client: picks the scheduled ID for a given date; falls back when the date is missing.
- Python: surprise of population against itself = 0; each check on a small toy dataset.
- `pnpm test` fails when fewer than 14 days are scheduled from today. (Vercel builds don't run tests, so this never blocks a deploy.)

## 6. Data model

**Candidate** (`candidates.json`): a full `Puzzle` record without an `id`, plus:

```json
{
  "key": "coffee|BRA|triangle|target|50",
  "dataset": "coffee",
  "rank": 0.41,
  "checks": {
    "cells": 18342,
    "top_cell_share": 0.031,
    "surprise": 0.41,
    "reach_pct": 88.2,
    "smallest_km": 410,
    "best_pct": null,
    "best_net": null,
    "naive_median": 38,
    "passed": true,
    "failed": []
  },
  "difficulty": "medium",
  "inputs_hash": "9f2c1e"
}
```

**Curated entry** (`puzzles/curated.json`, keyed by candidate key):

```json
{
  "coffee|BRA|triangle|target|50": {
    "status": "approved",
    "id": 57,
    "fact": "The smallest circle we found holding half of Brazil's coffee drunk has a radius of 410 km.",
    "prompt": null,
    "reason": null,
    "decided": "2026-12-02"
  },
  "beer|DEU|circle|budget|300": {
    "status": "rejected",
    "reason": "boring",
    "note": "same as the Germany target puzzle",
    "decided": "2026-12-02"
  }
}
```

`prompt: null` means use the generated prompt.

## 7. Out of scope

- **Pick the countries** mechanic: its own PRD. The key format already allows mechanic `countries`.
- New datasets: separate tasks. The generator picks them up once they're in the registry.
- Real player scores for difficulty (needs PostHog, still open in Phase 0).
- Server-side scoring, accounts, leaderboards (Phase 3).
- Weekly fresh-data puzzles, player-made puzzles (Phase 5).
- Publishing without approval. Max plays every puzzle before players see it.

## 8. Success metrics

| Metric | Target | Measured by |
|---|---|---|
| Approved puzzles | 150 at Phase 1 exit, 300 before Phase 4 | Count in `puzzles.json` |
| Days scheduled ahead | 60 at Phase 1 exit, never under 14 | Schedule test |
| Daily repeats | 0 | Schedule file |
| Curation time | ≤ 30 minutes per week of puzzles | Max's own estimate |
| Approval rate of the top 20 candidates | ≥ 50% | `curated.json` |
| Broken puzzles reaching players | 0 | Feedback, bug reports |
| Difficulty accuracy (after Phase 2) | Median real score of Hard puzzles ≥ 20 points below Easy | PostHog |

## 9. Milestones

The schedule must be live before **28 Dec 2026**.

| # | Deliverable | Date |
|---|---|---|
| M1 | Registry, enumeration, cheap checks, `candidates.json` | 30 Nov |
| M2 | Best shapes, difficulty, prompt templates, calibration on the 40 | 7 Dec |
| M3 | Review page, `curated.json`, `publish` | 14 Dec |
| M4 | Schedule, client reads it, archive change, 20+ approved so days 41 to 60 are covered | 21 Dec |
| M5 | 150 approved, 60 days ahead (needs more datasets from the backlog) | Phase 1 end, mid Jan 2027 |

## 10. Risks

| Risk | Mitigation |
|---|---|
| Surprise score misses what's actually fun | Human approval stays. Calibration on the 40 (§5.5). |
| Food stats look alike (all population × kg per person) | 7-day dataset spacing; rank favours datasets with fewer approved puzzles; add the backlog datasets. |
| Triangle search is slow | r3 aggregates, cache, survivors only. |
| Generator slips past 28 Dec | Hand-write 10 more puzzles in `PUZZLES` (the current flow) as insurance by 14 Dec if M2 is late. Worst case the daily cycles, which repeats but doesn't break. |
| A dataset's licence turns out to be non-commercial | `licence_ok` gate; each new dataset gets its licence checked before it is set. |

## 11. Open questions

1. ~~GLW livestock licence per species file~~ Resolved 20 Nov 2026: the five 2015 species records are CC0 on Dataverse and the FAO catalogue says CC BY 4.0. Both allow commercial use; credit Gilbert et al. 2022.
2. All thresholds in §5.3 and §5.4 are guesses until calibration.
