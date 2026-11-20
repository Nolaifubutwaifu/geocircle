# GeoCircle product roadmap v1

Written 19 Nov 2026. Living document: update it at the end of each phase.

## Status (20 Nov 2026)

Live since 18 Nov 2026 with 40 handwritten daily puzzles (they run out after 27 Dec). Current phase: end of Phase 0, Phase 1 underway. Day-to-day status and next steps: [CLAUDE.md](CLAUDE.md).

| Item | Status |
|---|---|
| Phase 0: mobile polish, PWA, share image | Done |
| Phase 0: PRD #1 puzzle generator | Done ([PRD](PRD-puzzle-generator.md)) |
| Phase 0: domain, PostHog, competitor analysis | Not started |
| Phase 1: generator, review page, schedule (PRD M1 to M4) | Done, merged 20 Nov 2026 ([PR #1](https://github.com/Nolaifubutwaifu/geocircle/pull/1)) |
| Phase 1: fun facts per dataset | Done, 37 approved |
| Phase 1: 150 approved puzzles, 60 days scheduled (PRD M5) | 0 approved so far |
| Phase 1: Pick the countries, 4 to 6 new datasets | Not started |

## 1. Goals

1. **Real players who enjoy it.** People come back on their own and send it to friends.
2. **Revenue.** Small at first. The aim is a game that pays for itself, then makes some money.
3. **Portfolio (side benefit).** Show product thinking: research, metrics, decisions. This is a by-product of the work above, not extra work.

## 2. Who it is for

**Core player: geo and data nerds.** People who like maps, odd statistics and a hard puzzle. r/geography, r/dataisbeautiful, r/MapPorn, map YouTube and TikTok. They want depth: tight scoring, leaderboards, weird datasets, an "optimal answer" reveal.

**Entry point: the daily habit.** A daily puzzle with a number, a streak and a share card brings in casual players. The depth is what keeps them.

**Side quests (later, small bets):**
- **Friends and party:** challenge links, friend leagues, live rooms.
- **Classrooms:** teacher packs and school licences.

## 3. Constraints that shape every decision

| Constraint | What it means |
|---|---|
| A few hours a week | Automate everything that repeats: puzzle generation, scheduling, quality checks. Claude writes most of the code; your time goes to curation, playtesting and posting. No features that need daily manual moderation. |
| Low budget | Static data files on Vercel (already paid). Neon Postgres free tier + Better Auth for accounts. PostHog free tier for analytics. App Store account already exists. |
| No audience yet | Launch where curious people already gather. Build sharing in early. |

## 4. What we learned from GeoGuessr

| GeoGuessr | GeoCircle |
|---|---|
| Two-week side project, launched on Chrome Experiments, went viral | Launch small and early on Reddit and Show HN with a "real data, surprising answer" hook |
| Street View = unlimited content for free | Open datasets × modes × shapes × regions = hundreds of generated puzzles |
| Community maps gave depth and ownership | Player-made puzzles + a "most-loved puzzles" leaderboard (Phase 5) |
| Watchable game; streamers drove the growth | The heatmap reveal is the clip moment; add solution replays; contact map creators |
| High skill ceiling led to ranked play and esports | Precision leaderboards and a skill rating; duels as a side quest |
| Bought Seterra for schools | The classroom side quest |
| Google API costs pushed them toward paid (reported, unconfirmed) | Our cost per game is near zero, so the free tier can be generous |

## 5. Puzzles: how we get to hundreds

**Generator, not handcrafting.** One script combines:

`dataset × mechanic (circle / triangle / pick countries) × mode (target / budget / net) × scope (world / continent / country) × target %`

It then filters candidates with automatic checks:
- **Not trivial:** the best answer isn't "the biggest population centre" unless that's the joke.
- **Not impossible:** the target can be reached within the shape limits.
- **Surprise score:** how far the data's distribution is from plain population. Higher means more interesting.
- **Difficulty estimate:** used to schedule easy Mondays and hard Sundays.

You curate: approve candidates on the review page (`/dev/review`); the schedule fills the daily slots. Fun facts are written per dataset with a source (`puzzles/funfacts.json`) and approved once.

**Three kinds of interesting:**
1. **Surprising data:** meteorites (Antarctica, Sahara), lightning (eastern Congo), coffee grown vs coffee drunk.
2. **New mechanics:**
   - **Pick the countries** (new): tap every country where X is true.
   - Later: line or border cut, two circles, avoid-zones, "guess the dataset from the heatmap", this week's fires or lightning.
3. **Funny data:**
   - "Pick every country with more kangaroos than people" (answer: only Australia)
   - "More sheep than people" (New Zealand, Mongolia, Uruguay, Australia, …)
   - "More chickens than people" (almost all of them: the trick is the opposite)
   - McDonald's vs Starbucks, cows minus people, and so on.

**Content backlog** (licences already checked in GAME-SPEC §4):
- In the game now: FAO foods, livestock, earthquakes, McDonald's, population, halve a country.
- Next: SPAM crops, LIS/OTD lightning, meteorites, WRI power plants, more Overture brands.
- Weekly fresh data: NASA FIRMS fires, GOES lightning.

**Target:** 300+ quality-checked puzzles before premium launch (Phase 4).

## 6. Viral factor

Ranked by how much it should help per hour of work:
1. **Challenge-a-friend link:** "I got 87 on this, beat me." The friend plays the same puzzle, and both see shapes overlaid. Cheap to build; every play invites someone.
2. **Share card:** text (`GeoCircle #142 🦘 87/100 🔥12`) plus an image preview of the map without spoilers when the link is posted.
3. **Funny-data headlines** that people forward without playing ("only one country has more kangaroos than people").
4. **Streaks and "you beat 73% of players today".**
5. **Creator outreach:** send map and geography TikTok and YouTube creators a hard puzzle and a "streamer mode" (no spoilers, big reveal).
6. **Community puzzles** (Phase 5): creators share their own puzzles.

## 7. Phases

Rough timing assumes a few hours a week with Claude doing most implementation.

### Phase 0: Foundations (weeks 1–3)
- Pick a domain, connect it to Vercel.
- PostHog analytics: puzzle started / submitted / shared / returned. Set up the first dashboard.
- Mobile polish pass, PWA install, OG share image.
- **Competitor analysis** (GeoGuessr, Worldle, Globle, Travle, GeoSlice): one page.
- **PRD #1: puzzle generator.**
- *Done when:* events show up in the dashboard and the game feels good on a phone.

### Phase 1: Puzzle engine (weeks 3–8)
- Generator with quality checks and a daily schedule (difficulty by weekday).
- New mechanic: **Pick the countries** (needs only per-country data).
- Add 4–6 new datasets from the backlog.
- Difficulty labels; random play from the generated pool.
- *Done when:* 150+ puzzles pass checks and 60 days are scheduled ahead.

### Phase 2: Soft launch and learn (weeks 8–11)
- Challenge-a-friend links (side quest #4, cheap part).
- One-question feedback after the reveal ("too easy / just right / too hard" + optional comment).
- 5 playtests (friends, classmates: watch them play, note where they get stuck).
- Launch posts: r/geography, r/dataisbeautiful, r/MapPorn, Show HN.
- *Done when:* 100+ real players and first retention numbers.

### Phase 3: Accounts and leaderboards (weeks 11–17)
- Neon Postgres + Better Auth: anonymous first, optional sign-in with Google, Apple or email to keep streaks across devices.
- **Server-side scoring** (needed because the data files are public).
- Per-puzzle leaderboards with skill tie-breakers (smallest circle that hits the target, best catch with a fixed budget).
- Profiles, stats history, a skill rating across puzzles.
- *Done when:* leaderboards are live and cheating is impractical.

### Phase 4: Depth and premium (weeks 17–24)
- Archive: free = last 7 days, premium = everything.
- Premium: unlimited random puzzles, hard modes, detailed stats, weekly fresh-data puzzles, no ads.
- Payments with Stripe on the web.
- Starting price (to test): about **$3/month or $20/year**, maybe a lifetime option.
- **Pricing and monetization analysis** doc.
- *Done when:* the first paying user.

### Phase 5: Community puzzles (months 7–9)
- Puzzle maker: choose dataset, region, mechanic and target. The engine validates it, so there's no broken puzzle and little to moderate (titles go through a word filter).
- Likes, a "most-loved puzzles" leaderboard, featured community puzzle of the week.

### Phase 6: App Store (months 8–10)
- Wrap the web app with Capacitor.
- Apple sign-in is required on iOS when other social logins exist.
- Subscriptions in the app must use in-app purchase. RevenueCat (free tier) keeps web and app subscriptions in sync.
- Ship iOS first, Android after.

## 8. Side quests (park until the core works)

| Side quest | Cheap first step | Bigger version | When |
|---|---|---|---|
| Friends and party | Challenge links (Phase 2) | Friend leagues, live rooms with overlaid shapes (needs real-time) | After Phase 4 |
| Classrooms | "Teacher pack" page: 10 puzzles per topic with explainers | Classes, join codes, teacher dashboard, school licence (children's privacy rules apply) | Once there are 300+ puzzles |
| B2B / sponsored | Embeddable daily puzzle widget for blogs or news | Sponsored datasets (NGOs, data companies) | When traffic justifies it |

## 9. Money

Order: **premium subscription first, then one-time packs, then ads, then B2B.**

- **Ads** pay little at small traffic and make the game feel cheaper. Turn them on only for free players, and only once daily players reach the thousands.
- **Rough math** (assumptions, not promises): 1,000 daily players ≈ 3,000 monthly. If 2% subscribe at about $3 net, that's about **$180/month**, enough to cover costs and prove the model.
- Growth past that comes from community puzzles, creators and the app stores.

## 10. Metrics

**North-star metric:** weekly players who finish at least 3 puzzles.

| Metric | What it tells us | 6-month goal |
|---|---|---|
| Daily players | Reach | 300–1,000 |
| D1 / D7 retention | Do people come back? | 30% / 12% |
| Share rate (shares ÷ finished puzzles) | Viral pull | 5%+ |
| Challenge link → new player | Viral loop strength | 20%+ of clicks play |
| Feedback "just right" | Difficulty tuning | 50%+ |
| Free → premium conversion | Business model | First 10 paying users |

## 11. Portfolio by-products (low priority)

Each one falls out of a phase. None should block the game.

| Artifact | What it is | From |
|---|---|---|
| Competitor analysis | One page on who else exists and how GeoCircle differs | Phase 0 |
| PRD (Product Requirements Document) | A 1–3 page spec for a feature: the problem, who it's for, what it must do, how we measure success, what's out of scope | Phases 0 and 3 |
| Metrics dashboard | The PostHog dashboard from §10 | Phase 0 onward |
| Pricing analysis | Why this price, what we tested, what happened | Phase 4 |
| Case study | "GeoCircle from 0 to N players": decisions, numbers, lessons | After Phase 4 |

## 12. Decisions made

- Core player: geo and data nerds. Daily habit as the entry point.
- Generated puzzles, curated daily pick; player-made puzzles later.
- Web, mobile-first now; App Store later.
- Backend: Neon Postgres (free tier) + Better Auth on Vercel. No vendor lock-in. Fallback: Supabase.
- Analytics: PostHog free tier.
- Building in public: optional, not planned.

## 13. Open questions

- Domain name: geocircle.com / .app / .io? Check availability.
- Does "GeoCircle" still fit once there are triangles and country picking? (Probably yes: the circle is the brand.)
- Kangaroo and similar "funny" datasets: find sources with licences that allow commercial use.
- Premium price: confirm by testing in Phase 4.
