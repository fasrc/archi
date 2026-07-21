## Why

The FASRC RAGAS golden set (`examples/benchmarking/fasrc_ragas_queries.json`, 105 rows) is
the ground truth for every benchmark run, but it silently falls out of sync with a **moving
KB**. New KB pages get zero questions (coverage gaps grow as the KB grows); pages the bank
already covers change, so stored reference answers go stale (**fact drift** — already real:
`anchor_questions.json` still ships `--gres=gpu:N` and `/n/holyscratch01` after the KB moved
to `--gpus` and `/n/holylabs`); and removed pages leave orphaned rows. There is also **no
machine-queryable way** to tell a confirmed answer from an unverified draft — "trustworthy"
lives only as free text in `notes`. Nothing detects any of this today, so the golden set
decays relative to the KB while looking healthy.

## What Changes

- **New maintenance capability** that diffs the ingested KB corpus (each doc's `url` +
  content hash, already in Postgres) against what the bank covers and emits three
  **human-gated** work lists: **coverage gaps** (new/uncovered pages), **fact drift** (locked
  rows whose grounding page changed), and **orphans** (rows citing a removed page).
- **Machine-queryable confirmation state.** Bank rows gain `status: draft | locked` plus a
  `source_hash` snapshot captured at lock time. The existing 105 rows and
  `anchor_questions.json` backfill to `draft`. Fields are loader-compatible (the harness
  requires only `user_input` and already tolerates extra fields), so **no benchmark behavior
  changes**.
- **Deterministic drift.** A locked row whose stored `source_hash` no longer matches the live
  corpus hash for its `sources` URL is re-fetched and model-diffed against its stored
  `reference`, then **flagged (never auto-edited)**.
- **Coverage is report + propose-for-greenlit.** The tool lists uncovered corpus URLs; for the
  pages an operator greenlights, it drafts grounded candidate questions as `status: draft` for
  human confirmation. It does not auto-cover every page.
- **One dev-side tool** `scripts/benchmarking/goldenset_maintenance.py` (subcommands
  `coverage` / `drift` / `orphans` / `report`), flag-only, reusing the harness's
  single-source-of-truth `src/utils/benchmark_schema`. A **cron job on the dev server** runs
  `report` read-only.
- **A model-facing skill** (`~/.claude/skills/archi-ragas-goldenset/` — a personal tool, **NOT
  in this repo/PR**) orchestrates the modes and gates every mutation on human confirmation.
- **Guardrail:** the tooling and the skill **never add, lock, edit, or delete a row
  unattended** — they propose, a human disposes. That is what keeps a golden set golden.
- **No default behavior change.** Benchmark runs, per-metric eligibility, and scoring are
  identical; gating eligibility on `status: locked` is explicitly deferred to a future change.

## Capabilities

### New Capabilities
- `ragas-goldenset-maintenance`: keep the RAGAS golden-set bank aligned to a moving KB —
  coverage-gap, fact-drift, and orphan detection driven by the ingested-corpus diff; a
  machine-queryable `draft`/`locked` confirmation state with a `source_hash` drift tripwire;
  human-gated proposals (never unattended mutation); and a cron-driven read-only report on the
  dev server.

### Modified Capabilities
<!-- none — the bank's optional status/source_hash fields are loader-compatible with
     benchmark-bank-preflight (which already tolerates extra fields), and benchmark
     eligibility/scoring is unchanged. Gating eligibility on `locked` is deferred to a
     future change, out of scope here. -->

## Impact

- **Code:** new `scripts/benchmarking/goldenset_maintenance.py` (`coverage` / `drift` /
  `orphans` / `report`; **injectable** HTTP-fetch + LLM-diff clients so tests are hermetic),
  reusing `src/utils/benchmark_schema` for bank load/normalize and the existing Postgres
  access path (mirroring the read-only corpus read the ingestion-verifier uses) for corpus
  `url` + content hash. Dev-side tooling like `validate_queries.py`; **not** shipped in the
  `pip install .` package, so **no redeploy/dependency trap**.
- **Data:** `examples/benchmarking/fasrc_ragas_queries.json` and `anchor_questions.json` gain
  `status` (backfilled `draft`) and, on lock, `source_hash`.
- **Infra:** one **cron entry on the dev server** runs `goldenset_maintenance.py report`
  read-only, writing a coverage/drift/orphan report (e.g. under `.ralph/log/`); no mutation,
  no deploy coupling, no coupling to the Ralph nightly loop.
- **Skill:** `~/.claude/skills/archi-ragas-goldenset/SKILL.md` — global personal tool,
  authored in Loop 2, **not part of this PR**.
- **Docs:** `examples/benchmarking/fasrc_ragas_queries.README.md` (the `status`/`source_hash`
  field + the maintenance loop) and `docs/docs/benchmarking.md` (the operator workflow),
  updated in the same change.
