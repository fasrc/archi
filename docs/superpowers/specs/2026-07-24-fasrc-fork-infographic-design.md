# FASRC fork infographic — design

**Date:** 2026-07-24 · **Audience:** skip-level management · **Source material:**
`docs/docs/fasrc_fork.md` (inventory snapshot at `origin/dev` = `0ed6fe66`, PR #150)

## Purpose

One 16:9 slide that does two jobs at once: orient leadership on what the
fasrc/archi fork *is* (a fork of the open-source archi framework that we now
develop at our own pace) and show what the team's investment *bought* (value
pillars + a measured velocity story). Chosen over two separate infographics to
keep it glanceable.

## Deliverable & production path

- **Master:** a self-contained 1920×1080 HTML file (all CSS inline, no external
  assets, `system-ui` font stack). Numbers live in one clearly-marked data block
  so refreshes are one edit.
- **Export:** rendered in Chrome and captured as a 2× PNG (3840×2160) for import
  into the Google Slides deck.
- **Speaker notes:** a plain-text block (below) pasted into the slide's notes.
- **Location:** working files under the session scratchpad `deck/` folder;
  final HTML + PNG handed to the user for the deck. Not committed to the repo
  (management artifact, not product code) unless the user asks.

## Layout (16:9, top to bottom)

1. **Header band** — title left, two stat chips right.
2. **Velocity ribbon (~26% height)** — weekly bar chart left (~70% width), big
   callout right. Style "L": gray bars pre-automation, crimson after, two dashed
   event markers labeled ① ②.
3. **Five pillar cards** (equal-width grid, ~40% height).
4. **Footer strip** — repos, snapshot date, partial-week asterisk.

Visual identity: white background, near-black text, Harvard crimson `#A51C30`
as the only accent color, light-gray card fills. No logos (the deck's theme
carries branding).

## Final copy

- **Title:** Archi at FASRC: what our fork delivers
- **Subtitle:** Built on the open-source archi framework — everything below was
  added by our team, Feb–Jul 2026
- **Chips:** `37 new capabilities` · `~110 changes shipped`
- **Chart label:** Changes shipped per week — ① dev loop + adversarial review
  (Jun 23) ② review required (mid-Jul)
- **Callout:** **3.7×** — weekly shipping pace since the dev loop + adversarial
  review came online
- **Pillars:**
  1. **Better answers** — smarter document retrieval, measured **+19% answer
     quality** in an A/B benchmark; every answer cites its sources as clickable
     links.
  2. **Built for FASRC** — assistant grounded in our own 370-page knowledge
     base; refuses and refers to rchelp rather than guessing.
  3. **Open platform** — standard chat API so other interfaces can plug in;
     Harvard-hosted AI models keep data inside Harvard.
  4. **Measured quality** — 105-question benchmark scored by AI and human
     graders; a nightly job flags stale or drifted source docs.
  5. **Engineering discipline** — every change ships with automated tests and
     review; supervised overnight automation handles routine work, humans
     review and merge.
- **Footer:** fasrc/archi + fasrc/archi-config · snapshot Jul 2026 · *latest
  week in progress

## Chart data (frozen for this snapshot)

Weekly first-parent merges to `origin/dev` since divergence from upstream
(`git log --first-parent --format='%ad' --date=format:'%Y-%U'
upstream/main..origin/dev | sort | uniq -c`), weeks with zero merges included:

| 2026 week (starting) | merges | | 2026 week | merges |
|---|---:|---|---|---:|
| Feb 15 (w07) | 2 | | Jun 07 (w23) | 3 |
| Mar 15 (w11) | 4 | | Jun 14 (w24) | 6 |
| w08–10, w12–19 | 0 | | **Jun 21 (w25)** | **32** |
| May 17 (w20) | 3 | | Jun 28 (w26) | 22 |
| May 24 (w21) | 10 | | Jul 05 (w27) | 4 |
| May 31 (w22) | 2 | | Jul 12 (w28) | 15 |
| | | | Jul 19 (w29)* | 16 |

- **Marker ①** Jun 23: Ralph-loop harness baseline merged (PR #31, commit
  `70d524bb`); loop headless-operational Jun 25–26; first
  "address adversarial review" commit Jun 24 (`f3b6e570`). The two practices
  arrived together and are one marker.
- **Marker ②** week of Jul 19: adversarial review formalized as a required
  pre-PR gate (team workflow records, Jul 19–24). Process date, not a repo
  commit.
- **Callout math:** pre-① active-week mean = (3+10+2+3+6)/5 ≈ 4.8/wk; post-①
  mean = (32+22+4+15+16)/5 = 17.8/wk → **3.7×**. Bars w07–w24 gray, w25+ crimson.
- **Chip provenance:** 37 `[capability]` + 28 `[fix]` tags counted in
  `docs/docs/fasrc_fork.md` Part I; ~110 changes shipped ≈ 119 first-parent
  merges minus housekeeping. Refresh both when the doc is regenerated.

## Speaker notes (paste into Slides)

- Velocity: the loop and adversarial review landed within 48 hours of each
  other, so their individual effects aren't separable from this data. The
  Jul-05 dip to 4 is real (large design PRs that week). The last week shown is
  partial. Monthly view: 207 fork commits vs upstream's 83 since divergence
  (~2.5× pace on the same codebase).
- Making review *required* (②) did not dent throughput — 15–16/wk since.
- +19% answer quality: controlled two-arm A/B, live FASRC corpus (330 docs per
  arm), 21-question bank, four RAGAS metrics, independent judge (Claude via
  HUIT Bedrock). Mean 0.569 → 0.677 (+0.108, +19%); biggest gains: context
  recall 0.603 → 0.810, context precision 0.491 → 0.652; cost +2 s/question.
  Full table + caveats: `docs/decisions/0003-hierarchical-rerank-default-on.md`.
  Caveats if pressed: n=21 (the seed bank; now 105 questions), references were
  DRAFT at the time (relative delta robust, absolutes provisional), refusal
  questions dipped on n=3, single run.
- **Bank-comparison rule:** scores from the 21-question era must never be
  charted against 105-question-era scores as a trend — the difficulty mix
  flipped (48%→28% easy; 38%→70% reasoning), references were confirmed/edited,
  and the corpus changed. Only within-run A/B deltas, or a fresh A/B on the
  current bank, are comparable.
- Automation guardrails: overnight runs never merge; a PR is the deliverable
  and a human merges in daylight. 37 new capabilities + 28 hardening fixes in
  fasrc/archi alone; the ops platform lives in fasrc/archi-config.

## Out of scope

No second slide; no animation; no Google-Slides-native rebuild (PNG import
preserves fidelity); deep detail stays in `fasrc_fork.md`. A re-run of the A/B
on the 105-question bank is a recommended follow-up, not part of this artifact.

## Risks / edge cases

- **Numbers drift:** the footer's snapshot date scopes every figure; the data
  block + provenance commands above make refresh mechanical.
- **July is partial:** asterisk on the last bar and footer; never extrapolate it.
- **Crimson/gray only** — the chart must stay legible in grayscale print
  (bar position, not hue, carries the pre/post distinction via the markers).
- **Claim safety:** every number on the slide traces to a command or a decision
  record cited above; the two strongest claims (+19%, 3.7×) both carry their
  method in the speaker notes.
