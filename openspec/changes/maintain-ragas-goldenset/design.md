## Context

The FASRC RAGAS golden set (`examples/benchmarking/fasrc_ragas_queries.json`, 105 rows;
`anchor_questions.json`, 5 oracle rows) is the ground truth the benchmark harness
(`src/bin/service_benchmark.py`) scores every run against. It was seeded on 2026-06-28 from 21
questions grounded in live `docs.rc.fas.harvard.edu` KB pages and has since grown to 105
(`easy_retrieve` 29 / `reasoning` 73 / `should_refuse` 3).

The bank is not maintained against the KB it mirrors. Three failure modes accrue silently:

```
   corpus (Postgres: url + source labels)         bank rows (sources[], status, source_hash)
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
- Make drift deterministic and cheap via a `source_hash` tripwire, escalating to an LLM diff
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

### D1 — Confirmation state is a real field (`status` + `source_hash`), not a `notes` convention

`status: draft | locked` makes confirmation queryable and drives drift detection; on lock the
tool snapshots `source_hash` = its own content hash of the row's grounding **source** (the
corpus identifier is URL-only, so it cannot serve here — see D2). Absent `status` ⇒ treated as
`draft` (conservative: an unproven row is not authoritative). Existing rows and
`anchor_questions.json` backfill to `draft`.

- *Why over parsing `notes`:* the current "confirm with operator before locking" text is
  unqueryable and phrasing-fragile; a field lets a census and drift logic be exact.
- *Why loader-safe:* the harness requires only `user_input` (+ `sources` for SOURCES) and
  ignores extra keys, so adding fields changes no benchmark behavior. Verified against
  `benchmark-bank-preflight`'s `required_fields_for_modes`.
- *Alternative rejected:* a sidecar lockfile mapping row→status. Rejected — it desyncs from the
  bank under edits; co-locating the state in the row is self-describing.

### D2 — Drift = content hash of the re-fetched **source**, then LLM diff (not the corpus hash)

A locked row stores `source_hash` = a content hash the tool computes over its grounding
**source** at lock time. Each drift check re-fetches that source (git blob raw content / KB
page text), re-hashes, and compares — no "last run" state file, no timestamps. Only on a
mismatch does the tool ask the model whether the stored `reference` still holds. Draft rows are
skipped.

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
content hash through the existing read-only Postgres path (mirroring the ingestion-verifier).
HTTP-fetch and LLM-diff clients are **injected** so tests are hermetic (fake corpus, fake
bank, fake fetch/LLM). The model-facing `~/.claude/skills/archi-ragas-goldenset/SKILL.md`
(personal, not in this PR) drives `coverage` / `drift-confirm` / `report` modes. A cron entry
on the dev server runs `report` read-only, writing to `.ralph/log/`.

- *Why scripts/ not src/:* dev/operator tooling like `validate_queries.py`; keeping it out of
  the package avoids a shipped dependency and the redeploy/`ModuleNotFoundError` trap.
- *Why cron over the Ralph nightly loop:* the operator chose a standalone dev-server cron —
  decoupled from the nightly automation's rails, read-only, no deploy coupling.

## Risks / Trade-offs

- **The corpus identifier is URL-only** → `ScrapedResource.get_hash()` hashes only the URL, so
  there is no corpus content hash for drift to key on (the same URL-keyed dedup is why a plain
  re-ingest keeps stale chunks). Mitigation (D2): drift computes its own content hash over the
  re-fetched **source**; the corpus is used only for URL-level coverage/orphan.
- **High-volume per-file sources** → a code repo (e.g. `User_Codes`) floods coverage with
  hundreds of blob URLs. Mitigation: group/filter coverage by `source_type`/`parent`;
  greenlight per source or path glob, never the whole repo at once.
- **URL slug mismatch** (the README's SOURCES-mode caveat: sitemap slugs vs stored `url`) →
  coverage/orphan diffs could mis-match a covered page as uncovered. Mitigation: normalize URLs
  (trailing slash, scheme) on both sides before diffing; surface "near-miss" URLs in the report
  rather than silently classifying.
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
