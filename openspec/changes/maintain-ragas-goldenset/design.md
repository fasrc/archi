## Context

The FASRC RAGAS golden set (`examples/benchmarking/fasrc_ragas_queries.json`, 105 rows;
`anchor_questions.json`, 5 oracle rows) is the ground truth the benchmark harness
(`src/bin/service_benchmark.py`) scores every run against. It was seeded on 2026-06-28 from 21
questions grounded in live `docs.rc.fas.harvard.edu` KB pages and has since grown to 105
(`easy_retrieve` 29 / `reasoning` 73 / `should_refuse` 3).

The bank is not maintained against the KB it mirrors. Three failure modes accrue silently:

```
   corpus (Postgres: url + source labels)         bank rows (sources[], status, source_hashes)
                 │                                              │
                 └───────────────── diff ───────────────────────┘
                                     │
     ┌───────────────────────────────┼───────────────────────────────┐
     ▼                               ▼                               ▼
  COVERAGE                         DRIFT                          ORPHAN
  corpus url in no row      locked row: re-fetch source,    row url not in corpus
  → gap; greenlit page      new content hash ≠ stored →     → flag for prune /
  → draft candidates        model diff → flag stale         convert to should_refuse
```

Grounding is already in the data: every row carries its `sources` URL, and the ingested
corpus stores each page's `url` plus `source_type`/`parent` labels. (Note the corpus's own
resource identifier `ScrapedResource.get_hash()` is **URL-only**, not a content hash — it
hashes `self.url` and never the content — so content-change detection must hash the source
itself; see D2.) The harness's schema rules live in one place,
`src/utils/benchmark_schema` (`normalize_bank`, `preflight_bank_file`,
`required_fields_for_modes`), and the existing dev-side validator
`scripts/benchmarking/validate_queries.py` delegates to it rather than reimplementing.

Constraints: the golden set must stay trustworthy, so no unattended process may mutate it;
benchmark loading/scoring must not change; the maintenance tool is dev/operator tooling (like
`validate_queries.py`), not part of the shipped `pip install .` package, so it introduces no
runtime dependency and no redeploy trap.

## Goals / Non-Goals

**Goals:**
- Detect the three ways the bank falls out of sync with the KB — coverage gaps, fact drift,
  orphans — from the ingested-corpus diff.
- Make "is this reference authoritative?" machine-queryable (`status: draft|locked`) instead of
  free-text `notes`.
- Make drift deterministic and cheap via a per-source `source_hashes` tripwire, escalating to an LLM diff
  only on a hash mismatch.
- Keep every mutation human-gated: the tool and skill propose; a human disposes.
- Run a read-only report unattended via cron on the dev server.

**Non-Goals:**
- Auto-adding, auto-locking, auto-editing, or auto-deleting bank rows. Never.
- Changing benchmark eligibility or scoring. Gating context-metric eligibility on `status:
  locked` (only scoring confirmed references) is a **separate, deferred** change.
- Maintaining the ServiceNow bank (`snow_ragas_queries_pt1.json`, gitignored/operator-local) —
  the public FASRC bank only.
- Building a new ingest or re-ingest path. The tool reads the corpus the last deploy produced.
- Shipping the maintenance tool inside the archi package.

## Decisions

### D1 — Confirmation state is a real field (`status` + `source_hashes`), not a `notes` convention

`status: draft | locked` makes confirmation queryable and drives drift detection; on lock the
tool snapshots `source_hashes` = a **map from each normalized `sources` URL to its own content
hash of that grounding source** (the corpus identifier is URL-only, so it cannot serve here — see
D2). A map, not a scalar, because rows are already multi-source — the MPI anchor in
`anchor_questions.json` cites both `running-jobs` and `mpi-message-passing-interface`; a single
per-row hash would silently miss drift in whichever page it didn't track. A `locked`
`should_refuse` row has empty `sources` and therefore records **no** `source_hashes` (nothing to
drift against) — locking must not require a grounding hash. Absent `status` ⇒ treated as `draft`
(conservative: an unproven row is not authoritative). Existing rows and `anchor_questions.json`
backfill to `draft`.

- *Scope of "not authoritative":* this governs the **maintenance tooling only** (drift/census).
  The benchmark harness is untouched — it keeps scoring every row's `reference` regardless of
  `status`. So backfilling 105 rows to `draft` changes no scores; the known-stale references
  keep scoring exactly as today and are surfaced by the new drift pass for an operator, not
  silently dropped. Gating scoring eligibility on `locked` is a **separate, deferred** change
  (Non-Goals) — attempting it here would change benchmark behavior, which this change forbids.
- *Why over parsing `notes`:* the current "confirm with operator before locking" text is
  unqueryable and phrasing-fragile; a field lets a census and drift logic be exact.
- *Why loader-safe:* the harness requires only `user_input` (+ `sources` for SOURCES) and
  ignores extra keys, so adding fields changes no benchmark behavior. Verified against
  `benchmark-bank-preflight`'s `required_fields_for_modes`.
- *Alternative rejected:* a sidecar lockfile mapping row→status. Rejected — it desyncs from the
  bank under edits; co-locating the state in the row is self-describing.

### D2 — Drift = content hash of the re-fetched **source**, then LLM diff (not the corpus hash)

A locked row stores `source_hashes` = a map of content hashes the tool computes over **each** of
its grounding sources at lock time. Each drift check re-fetches every `sources` URL (git blob raw
content / KB page text), re-hashes, and compares each against its stored entry — no "last run"
state file, no timestamps. Only on a mismatch does the tool ask the model whether the stored
`reference` still holds. Draft rows, and locked source-less `should_refuse` rows, are skipped.

- *Why hash the source, not the corpus:* `ScrapedResource.get_hash()` is **URL-only** (it
  hashes `self.url`, never the content), so the corpus has no content hash to compare — and
  that same URL-keyed identity is why a plain re-ingest silently keeps stale chunks (a nuke is
  required to overwrite). The corpus is authoritative for *which URLs exist* (coverage/orphan),
  but the **live source** is the only oracle for *content change*.
- *Cost:* the cheap tripwire is fetch+hash, run for every locked row each pass; the expensive
  LLM diff fires only for sources whose hash actually moved.
- *Alternative rejected — hash the ingested corpus content:* the URL-keyed dedup means a
  re-ingest may not update stored content under an unchanged URL, so corpus content can lag the
  live source; hashing it would miss real drift. A stale corpus is the ingestion-verifier's
  concern, not the golden set's.
- *Alternative rejected — re-run retrieval:* catches index drift, not fact drift (a stale
  answer whose page still retrieves passes).
- *Alternative rejected — hash-only, no LLM:* only says "source changed," forcing a human to
  read every changed source even when the change did not touch the answer.

### D3 — Coverage is report + propose-for-greenlit, not auto-cover

`coverage` diffs corpus URLs against the union of row `sources` and lists the uncovered pages.
`coverage --propose <url>` drafts grounded candidates (as `draft`) only for pages the operator
greenlights.

- *Why:* a golden set's value is signal-per-question, not count (it is already 105 with
  `reasoning` dominant at 73). Auto-covering every new page manufactures DRAFT debt and
  redundant questions on minor pages. The operator picks what earns coverage.
- *Git & other per-file sources:* a git source ingests one document **per file** (blob URLs
  like `.../User_Codes/blob/main/<path>`, ref-pinned to the branch so they are stable across
  commits), so a single source can add hundreds of uncovered URLs. Coverage therefore
  groups/filters by source (`source_type` / `parent`, both stored on each corpus doc) — e.g.
  `coverage --source User_Codes` — so greenlighting is per-source or per-path, not a flat
  firehose.

**Greenlight is conversational, not a file edit.** The operator greenlights through the skill's
coverage mode inside a Claude Code session: it groups the *newly* uncovered pages by source,
shows the answer-worthy shortlist, and the operator replies in plain language which to draft.
The unattended cron `report` only *detects and surfaces* gaps (so the operator knows work is
waiting); it never drafts. Detection is automated and asynchronous; the greenlight decision
stays a human, in-session act. The CLI `coverage --propose <url>` is the primitive the skill
calls under the hood (and keeps the behavior testable).

**Decision ledger (idempotency).** Because a conversational interface is stateless on its own,
the tooling persists a lightweight ledger of URLs an operator has explicitly **declined**, and
`coverage` suppresses only those. Crucially, the ledger records *declines only* — it does **not**
record "drafted/covered". *Covered* is re-derived from the current bank on every run (a URL is
covered iff some current bank row's `sources` references it after reconciliation). If the ledger
also suppressed "drafted" URLs, a page whose candidates were proposed but then abandoned or
rejected — never applied to the bank — would vanish from later coverage reports and read as
falsely clean. Re-deriving covered-ness from the bank keeps a greenlit-but-unapplied page
visible as a gap until a row actually lands. The ledger is the durable record of *declines* the
conversational path otherwise lacks.

### D4 — Human-gated mutation as an invariant, split from detection

Detection passes (`coverage`/`drift`/`orphans`/`report`) are pure readers that leave the bank
byte-unchanged. Applying a proposal (add a draft, lock a reference, prune an orphan) is a
distinct, human-initiated step. The skill orchestrates but always stops at a confirmation.

- *Why:* a golden set stops being golden the moment a bot mutates it unattended. Separating
  read from write makes the cron job trivially safe and makes "did anything change the bank?"
  answerable from git alone.

### D5 — One dev-side tool + one skill; reuse `benchmark_schema`; cron trigger

`scripts/benchmarking/goldenset_maintenance.py` with subcommands `coverage` / `drift` /
`orphans` / `report`, loading the bank via `benchmark_schema` and reading corpus `url` +
`source_type`/`parent` labels through the existing read-only Postgres path (mirroring the
ingestion-verifier — the corpus has no content hash, D2). HTTP-fetch and LLM-diff clients are
**injected** so tests are hermetic (fake corpus, fake bank, fake fetch/LLM). The model-facing
`~/.claude/skills/archi-ragas-goldenset/SKILL.md` (personal, not in this PR) drives `coverage` /
`drift-confirm` / `report` modes. A cron entry on the dev server runs `report` read-only,
writing to `.ralph/log/`.

- *Reuse #133's fetch/inventory building blocks:* the live-source fetch (drift re-fetch,
  candidate drafting) and the orphan inventory should reuse `sitemap_source`'s established
  pattern — the injected `FetchText` callable, `fetch_sitemap_text`'s timeout/size cap, the
  `is_url_allowed` trust filter, and `normalize_page_url` — rather than a second, divergent
  fetch/normalize path. Same house style, same SSRF posture, one normalizer.
- *Why scripts/ not src/:* dev/operator tooling like `validate_queries.py`; keeping it out of
  the package avoids a shipped dependency and the redeploy/`ModuleNotFoundError` trap.
- *Why cron over the Ralph nightly loop:* the operator chose a standalone dev-server cron —
  decoupled from the nightly automation's rails, read-only, no deploy coupling.

### D6 — The persisted corpus is not a reliable mirror; key content/removal on a fresh live signal

The ingested corpus lies to us in two directions, both rooted in the same URL-keyed identity
(`ScrapedResource.get_hash()` = md5 of the URL, D2):

- **It lags on edits.** A page edited in place keeps its URL, so a plain re-ingest may not
  overwrite stored content (a nuke is required). Corpus content can therefore be *staler* than
  the live page → hashing the corpus would **miss** real drift.
- **It keeps removed pages.** The collection path upserts by URL hash and does not prune URLs
  that vanish from a later sitemap/source list. A deleted page still appears in `documents` →
  absence-from-corpus would **miss** real orphans.

So the corpus is authoritative only for *coverage* (which URLs were ingested at least once).
**Content change (drift)** keys on a re-fetch of the live source; **removal (orphan)** keys on a
freshly expanded live source inventory (sitemap / source-list expansion), or requires an
explicit prune/nuke before corpus-presence is trusted as "still exists".

**The live inventory is now a first-class primitive (PR #133).** `sitemap_source.expand_sitemaps`
(landed on `dev` in #133) parses/normalizes/trust-filters/expands a `sitemap-<url>` source into
the page URLs the sitemap advertises *now*, with an **injected fetch** (hermetic tests, matching
D5) and `normalize_page_url` — the *same* canonical form the hand-list and corpus use (mirrors
`sources_builder.normalize_url`; #118). Orphan detection therefore reuses it directly:
`orphan ⇔ row.sources ∉ expand_sitemaps(current_sources)`, apples-to-apples against the corpus
`url` and bank `sources`. For a hand-listed (non-sitemap) source, the "expansion" is just the
current list. Either way the oracle is *today's* source list, not the never-pruning corpus.

- *Fail-open guard (a trap #133 introduces):* `expand_sitemaps` fails **open** per document (a
  fetch/parse failure logs a WARNING and contributes zero URLs) and raises below a floor.
  Fail-open is correct for *ingest* (ingest fewer pages), but as an **orphan oracle** it is
  dangerous: a failed sitemap shard makes a whole block of live pages look absent → a wave of
  **false orphans**. So orphan detection MUST treat an incomplete expansion (any document failed,
  or below floor) as an **operational failure and abstain** from orphan-flagging that run —
  folding into the report's existing "reserve non-zero exit for operational failure, not for
  findings" contract. Coverage/orphan run only against a *complete* inventory.

- *Why not compare drift against the persisted corpus (reviewer suggestion):* the benchmark
  answers from the corpus, so it is tempting to judge drift there. Rejected — because the corpus
  lags the live KB (above), that would silently pass a reference that is stale relative to the
  authoritative page. The intended drift semantic is *reference vs. authoritative live KB*;
  corpus staleness is the ingestion-verifier's concern, not the golden set's. The one real cost
  — re-fetching pages the benchmark never sees — is bounded by the hash tripwire (LLM fires only
  on a moved hash) and is the correct trade for not missing drift.

### D7 — Reconcile URLs with the ingest's own normalizer; near-miss → separate bucket

URL matching **reuses `sitemap_source.normalize_page_url`** (PR #133) rather than a bespoke
normalizer, so the maintenance tool canonicalizes URLs *identically* to how the ingest stored
them — scheme/host case, fragment, and the `/x` vs `/x/` trailing-slash variant (#118) all
collapse the same way on both sides. That removes the largest class of the README's SOURCES
caveat (slash/case/fragment drift) by construction: post-#133, corpus `url`, sitemap output, and
a canonicalized bank `sources` share one form.

What remains is the *narrower* residual — a genuinely different **slug** (e.g. a redirect alias:
the bank author linked `/kb/old-name`, the sitemap advertises `/kb/new-name` for the same page).
Exact-normalized matching won't unify those, so a URL that resolves to a covered page only by
such a near-miss goes to a separate **"needs reconciliation"** bucket — never a definitive gap or
orphan. That bucket is an explicit human signal ("these two URLs are probably the same page,
confirm"), safer than silently guessing either way. Reusing the ingest's normalizer also settles
the Loop-2 fetch-parity risk (below): don't reimplement normalization or extraction — share it.

## Risks / Trade-offs

- **The corpus identifier is URL-only** → `ScrapedResource.get_hash()` hashes only the URL, so
  there is no corpus content hash for drift to key on (the same URL-keyed dedup is why a plain
  re-ingest keeps stale chunks *and* keeps removed pages). Mitigation (D2/D6): drift computes its
  own content hash over the re-fetched **source**, and orphan detection keys on a freshly
  expanded live source inventory; the corpus is trusted only for "which URLs were ingested".
- **High-volume per-file sources** → a code repo (e.g. `User_Codes`) floods coverage with
  hundreds of blob URLs. Mitigation: group/filter coverage by `source_type`/`parent`;
  greenlight per source or path glob, never the whole repo at once.
- **Conversational greenlight could re-nag** → a stateless in-session interface would resurface
  the same skipped pages every run. Mitigation: the decision ledger (declined + drafted URLs);
  `coverage` shows only not-yet-decided gaps.
- **URL slug mismatch** (the README's SOURCES-mode caveat: sitemap slugs vs stored `url`) → a
  covered page could be mis-classified as *both* an uncovered gap *and* an orphan. Mitigation
  (D7): slug-aware reconciliation before classification, and any near-miss goes to a separate
  "needs reconciliation" bucket — never silently classified as a definitive gap or orphan.
- **LLM drift-diff false positives/negatives** → the model may over- or under-flag. Mitigation:
  drift output is advisory and human-reviewed (never auto-edits); the hash tripwire bounds the
  LLM to only changed pages.
- **Backfill churn** → adding `status: draft` to 105 rows is a large, mechanical diff.
  Mitigation: land the backfill as its own commit, separate from behavior, and assert the
  harness loads the backfilled bank identically (no scoring change).
- **`black` reflow trap on `benchmark_schema.py`** → an in-place edit to a large module can trip
  the diff-coverage gate. Mitigation: check the seam first (black-clean anchor) before editing;
  prefer adding a new helper over reflowing existing code.

## Migration Plan

1. **Backfill (data only):** add `status: draft` to all existing rows in
   `fasrc_ragas_queries.json` and `anchor_questions.json`; assert the harness loads and scores
   the bank identically. Reversible by dropping the field (loader ignores it).
2. **Tooling (additive):** add `goldenset_maintenance.py` (read-only subcommands first:
   `coverage`, `orphans`, `report`; then `drift` with injected fetch/LLM). No change to
   existing files beyond `benchmark_schema` helpers for `status`.
3. **Cron:** add the dev-server cron entry for `report` (read-only). Rollback = remove the cron
   line; nothing else depends on it.
4. **Skill (Loop 2, outside this PR):** author `archi-ragas-goldenset` with the human-gated
   apply steps.

Rollback at any stage is clean: the fields are loader-ignored, the tool is standalone, the
cron is one line.

## Open Questions

- **Corpus read path:** reuse a `postgres_service_factory` service vs. a minimal read-only
  query for `(url, content_hash)`? Prefer the existing service if it already exposes both.
- **Report sink + alerting:** `.ralph/log/` file is the baseline; is any notification wanted
  when drift count crosses a threshold, or is the file review enough? (Leaning: file only,
  per the read-only/no-spam cron contract.)
- **Candidate drafting model:** which model authors coverage candidates — the same provider the
  agent uses, or a fixed one for reproducibility? (Loop-2 detail; candidates are human-confirmed
  regardless.)
