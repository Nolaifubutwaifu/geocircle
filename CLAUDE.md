# Geocircle: notes for Claude sessions

Daily map puzzle (Next.js 16 on Vercel, project `geocircle-rmte`) plus a Python data pipeline. Product plan: [ROADMAP.md](ROADMAP.md). Game and dataset licences: [GAME-SPEC.md](GAME-SPEC.md). Puzzle generator spec: [PRD-puzzle-generator.md](PRD-puzzle-generator.md). Commands: [README.md](README.md).

Update the "Where things stand" and "Next steps" sections at the end of each working session.

## Where things stand (20 Nov 2026)

- **Live:** daily puzzles since 18 Nov 2026. The 40 handwritten puzzles cover 18 Nov to 27 Dec.
- **20 Nov 2026 (later):** Max approved 31 generated puzzles (IDs 41 to 71) in chat, then rejected 58 (land budget: maximising land is trivial, so the generator no longer makes land budget puzzles). `pnpm data schedule` runs to 19 Jan 2027 (61 days ahead), 7 approved puzzles spare. The reveal panel shows one fact (the sourced fun fact, else the generated one). The 3 net puzzles with a 1,500 km circle scored 100 for any guess, so they were cut to NZ sheep 300 km, USA chickens 500 km, Mongolia goats 500 km. New `trivial` gate (`GATE["max_naive"]=95`) rejects candidates where a typical first guess scores 95+. Land facts now read "about X km² of land" (hex areas overcount coasts, undercount Antarctica). All 41 fun facts approved. Old Vercel project `geocircle` deleted. After Max played them: legacy 31 and 32 (brick kilns) replaced by chickens USA (circle, half) and sheep Oceania (triangle, 25%), 39 (Africa gas power, a first guess scored 0 at any size) by potatoes Africa (triangle, 10%), 70 pigs vs people USA raised from 400 to 500 km. Same IDs and dates: nobody else had played them. Then the rest of the APAD industrial block went too: 33 pigs Brazil, 34 rice Africa, 35 cattle Mexico 400 km, 36 tea world, 37 chocolate world, 38 coffee world, 40 land Russia, and generated 69 (brick kilns India) became goats South America. The APAD datasets are `off=True` in `DATASETS`, so the generator no longer proposes them.
- **Bulk approval (20 Nov 2026):** Max asked to turn the candidates into puzzles. Approved 559 (IDs 72 to 630): the best-ranked variant per dataset, scope, shape and mode, skipping ones where a typical first guess scores under 10. Not played or reviewed one by one, so expect some duds: fix them as Max reports them. IDs are spread so datasets alternate. 629 puzzles in total, 566 unscheduled spares (playable via the archive and Next puzzle). The home page gets only scheduled puzzles and the archive a slim list, since the full `puzzles.json` is about 600 KB.
- **Start view fix:** worldwide puzzles opened centred on the Pacific. `auto_view` now centres worldwide data on Europe and Africa; 39 views fixed. `GEN_VERSION` 4.
- **20 Nov 2026 (latest):** batch 1 of [DATASET-RESEARCH.md](DATASET-RESEARCH.md) is in the pipeline: 12 more FAO foods (yams, cassava, plantains, dates, rye, pork, millet, sorghum, mutton, sweet potatoes, olive oil, apples) plus 4 point datasets in `step_points`: dinosaurs (PBDB, CC0), languages (Glottolog), lakes and lake area (HydroLAKES, stored at r5). New rule: a continent holding under 1% of a food (`GATE["continent_min_share"]`) is skipped ("Europe's cassava eaten"). `pnpm data candidates`: 1,459 pass, about 1,050 of them from the new datasets. No sourced fun facts for them yet, so their reveals show the generated fact. Max then asked for them as puzzles: `continent_min_share` raised to 5% (dropped 27 like "South America's sweet potatoes"), and the same rule as the bulk approval gave 215; Max asked to cut the too-hard ones, so those with a first-guess score under 20 or a target circle under 75 km went (71). 144 approved (IDs 631 to 774; 7 easy, 83 medium, 54 hard). Pork, plantains and yams have no puzzles left. 773 puzzles in total. 13 sourced fun facts added (one per new dataset with puzzles), each checked against its page; every puzzle now has one. Facts now say "DR Congo".
- **Comeback features (20 Nov 2026):** the reveal shows a daily streak (`lib/stats.ts`: a day counts only if that day's daily was played on that day), played count, average, best streak and a countdown to the next daily. Share text has the topic, the streak and a challenge link (`?beat=64&s=c,lng,lat,r` or `s=t,...`, parsed and validated in `lib/share.ts`): the friend sees "A friend scored 64" and, after playing, their own result against it plus the friend's shape dotted on the map. One-tap "How hard?" (too easy / just right / too hard) saves `feel` on the result. It goes to PostHog. The reveal panel can collapse to a one-line bar, and triangle side labels that would overlap are hidden.
- **PostHog (21 Nov 2026):** installed from the Vercel Marketplace (resource `posthog-aureolin-clock`, EU cloud, pay-as-you-go with the free allowance). Env vars `NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN` and `NEXT_PUBLIC_POSTHOG_HOST` are set on Vercel; `vercel env pull --yes .env.local` for local runs. `lib/analytics.ts` sends only `puzzle_submitted`, `puzzle_shared`, `puzzle_feedback`, `challenge_opened` and pageviews. Privacy-first: memory persistence (no cookie, nothing stored), no autocapture, no session recording, no surveys, so no consent banner. The cost: returning players can't be recognised, so D1/D7 retention needs a consent banner plus `persistence: "localStorage+cookie"` after consent.
- **Merged 20 Nov 2026** ([PR #1](https://github.com/Nolaifubutwaifu/geocircle/pull/1), merge commit `cf49bec`) and deployed to production by Vercel's git integration:
  - Generator (PRD M1 to M4): `pnpm data candidates` gave 2,112 passing candidates (2,009 after the approvals and the `trivial` gate), including livestock. Full run about 3 min, cached rerun about 10 s.
  - Review page `/dev/review` (dev only, 404 in production) writing to `puzzles/curated.json`.
  - `pnpm data publish` (legacy + approved + fun facts into `public/data/puzzles.json`) and `pnpm data schedule` (`public/data/schedule.json`).
  - Home page reads the schedule; `/archive` and Next puzzle hide future dailies when `SHOW_FUTURE` in `lib/puzzles.ts` is false. It is true since 20 Nov 2026 (Max wants every puzzle open): set it to false before a public launch. Next puzzle only opens unplayed ones.
  - Livestock (GLW 2015 cattle, sheep, pigs, chickens, goats) switched on: CC0 on each Dataverse record, checked 20 Nov 2026.
  - Fun facts: `puzzles/funfacts.json`, 41 sourced facts for 26 datasets, all approved.
  - Bug fixes: land layer stack overflow on large views (`lib/land.ts`); net facts for single-country scopes.

## Next steps, in order

1. **Calibration (end of M2):** Max sends `id quality difficulty score` lines in chat as he plays; they go to `puzzles/calibration-labels.csv` (committed, keyed by candidate key, so they survive reruns). 4 rows so far. Max fills `max_quality` (good / boring / too hard / broken) and `max_difficulty` (easy / medium / hard) for the 40 rows of `scripts/pipeline/out/calibration.csv` (git-ignored, regenerated by `pnpm data candidates`: back up his labels before rerunning). Then tune `GATE` in `build.py` until the checks agree with him on at least 80% (PRD §5.5). Known skews:
   - Budget-mode puzzles come out almost all Hard.
   - Target puzzles top out near Easy because of the radius noise.
   - Antarctica land puzzles score 0.
   - South Georgia and other empty areas rank top for earthquakes.
   - Several approved Hard puzzles have a first-guess score near 0 (USA pigs vs people, Africa McDonald's, USA earthquakes): watch the feedback.
2. **Net puzzles keep only one radius:** the generator picks the radius with the biggest net, which tends to be huge (covers the whole country). Now caught by the `trivial` gate, but a better rule would pick the radius closest to a target first-guess score.
3. **M5 / rest of Phase 1:** 150 approved puzzles, 60 days ahead. New datasets: ranked shortlist with licences checked in [DATASET-RESEARCH.md](DATASET-RESEARCH.md) (20 Nov 2026). Batch 1 (foods, dinosaurs, languages, lakes) is in, approved and has fun facts. Easy puzzles are scarce (7 of 144): watch Max's feedback. Next: GBIF animals, then SPAM crops, UN age data, CHELSA rain. Meteorites stay out: still no licence. Pick the countries mechanic (its own PRD). A second sourced fun fact for datasets that have only one.
4. **Phase 0 leftovers:** domain ("GeoCircle" is taken by another app at geocircle.vercel.app), a PostHog dashboard for the roadmap metrics, competitor analysis. Calibration can now use the `puzzle_feedback` events.

## Weekly routine

1. `pnpm data candidates`
2. Review and approve at `localhost:3000/dev/review`
3. `pnpm data publish`
4. `pnpm data schedule`
5. Commit and deploy.

## Gotchas

- Production is the Vercel project `geocircle-rmte` (deploys on push to `main`), live at geocircle.taigaprojects.space. The project named `geocircle` is the old one: don't deploy to it.
- `scripts/pipeline/build.py` runs with `uv run` (dependencies in its header). `pnpm data <steps>` is the alias. pnpm 12 rejects `pnpm -s`.
- `pnpm data selftest` holds the Python assert checks (scoring, prompts, publish, schedule, fun facts). `pnpm test` runs vitest.
- Bump `GEN_VERSION` in `build.py` when the measuring code changes, or the candidate cache (`scripts/pipeline/.cache/`) serves stale results.
- `public/data/puzzles.json` is generated: never hand-edit it. `public/data/schedule.json` may be hand-edited to swap days; the scheduler never changes an existing date.
- Puzzle IDs are never reused. IDs 1 to 40 stay fixed to 18 Nov to 27 Dec 2026. Before its date, a legacy puzzle's content may be replaced in `PUZZLES` (then `pnpm data puzzles publish`); `pnpm data puzzles` rebuilds all 40, so diff `public/data/puzzles.json` to confirm only the intended IDs changed.
- Every dataset needs its licence checked before `licence_ok=True`. GLW ducks, horses, buffalo and the 2020 files are unchecked.
- A dev-only console warning on `/play/[id]` (params read outside Suspense) predates the generator and doesn't affect players.
- Max is not a software engineer: explain technical choices plainly. No em dashes in player-facing text or docs.
