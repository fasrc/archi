> **Sequencing:** each numbered group is a separate, gate-green PR. Data backfill (group 1) is
> mechanical and lands before behavior. Detection subcommands (groups 2–4) are read-only and
> TDD'd with injected fetch/LLM + fixture corpus/bank. The cron (group 5) and skill (group 6)
> land last; the skill is a personal tool and rides **no** repo PR. Every code group is TDD:
> failing test first → minimum code → refactor; gate green (`bash scripts/gate.sh`, ≥80% diff
> coverage) before commit; branch from `origin/dev`, PR to `fasrc/archi:dev`.

## 1. Confirmation state: `status` / `source_hash` field + backfill (data + schema)

- [ ] 1.1 Check the `benchmark_schema` edit seam is black-clean before touching it (black-churn / diff-coverage trap); route through a new helper if not.
- [ ] 1.2 TDD: `benchmark_schema` treats a missing `status` as `draft` and preserves `status`/`source_hash` on load; assert the harness loads a bank with and without the fields identically (no eligibility/scoring change).
- [ ] 1.3 TDD: a `bank_status_counts(bank)` helper returns `locked`/`draft` counts and the `anchor_type` distribution from the field (not by parsing `notes`).
- [ ] 1.4 Backfill `status: draft` into all rows of `examples/benchmarking/fasrc_ragas_queries.json` and `examples/benchmarking/anchor_questions.json`; land as its own commit, separate from behavior.
- [ ] 1.5 Assert (test or scripted check) the backfilled bank scores identically to pre-backfill for a fixed fake run — field addition is behavior-neutral.

## 2. Corpus read + coverage/orphan detection (read-only, TDD)

- [ ] 2.1 TDD: a read-only corpus accessor returns the ingested URL set with each doc's `source_type`/`parent` labels, via the existing Postgres path (mirror the ingestion-verifier read); injectable/fakeable for hermetic tests. Do NOT rely on the corpus resource hash — `ScrapedResource.get_hash()` is URL-only.
- [ ] 2.2 TDD: URL normalization (scheme, trailing slash) applied to both corpus URLs and row `sources` before any diff; near-miss URLs surfaced, not silently misclassified.
- [ ] 2.3 TDD: `coverage` reports corpus URLs referenced by no row's `sources`; empty when every corpus URL is covered.
- [ ] 2.4 TDD: `orphans` flags rows whose `sources` URL is absent from the corpus; `should_refuse` rows (empty `sources`) are never flagged; nothing is deleted.
- [ ] 2.5 TDD: `coverage` groups and filters gaps by source (`source_type`/`parent`) so a high-volume git source (per-file blob URLs) doesn't flood the report; greenlight per source or path glob.
- [ ] 2.6 Wire `coverage` / `orphans` as subcommands of `scripts/benchmarking/goldenset_maintenance.py`, loading the bank via `benchmark_schema`.

## 3. Coverage candidate proposal (greenlit-only, TDD)

- [ ] 3.1 TDD: `coverage --propose <url>` drafts grounded candidate questions for a greenlit page as `status: draft`, with `sources` set and references left draft (LLM client injected/faked).
- [ ] 3.2 TDD: `--propose` never drafts for a non-greenlit page and never locks a proposed candidate.
- [ ] 3.3 TDD: proposals are emitted for review only — the bank JSON file is byte-unchanged by a `--propose` run (writing a candidate in is a separate human-applied step).

## 4. Fact-drift detection (hash tripwire → LLM diff, TDD)

- [ ] 4.1 TDD: `source_hash` is a content hash the tool computes over the re-fetched source (git blob raw / KB page text), NOT read from the corpus identifier (URL-only); a `locked` row whose stored `source_hash` differs from a fresh source hash is flagged, a matching hash is not.
- [ ] 4.2 TDD: `draft` rows are never drift-checked, regardless of hash.
- [ ] 4.3 TDD: on a hash mismatch the tool asks the injected LLM whether the stored `reference` still holds against the re-fetched source (injected fetch); output is advisory; `reference`/`status` are left unchanged.
- [ ] 4.4 Wire `drift` as a subcommand; confirm no detection path writes to the bank file.

## 5. Read-only `report` + dev-server cron

- [ ] 5.1 TDD: `report` prints coverage gaps, drift flags, and orphans, modifies no file, and exits zero when findings exist — reserving non-zero for operational failure (unreachable corpus).
- [ ] 5.2 Add the dev-server cron entry running `report` read-only, writing to `.ralph/log/`; document install + rollback (remove the line).

## 6. Skill + docs

- [ ] 6.1 Author `~/.claude/skills/archi-ragas-goldenset/SKILL.md` (personal tool, NOT in this repo PR): `coverage` / `drift-confirm` / `report` modes, every mutation gated on human confirmation (add draft, lock reference, prune orphan as explicit apply steps).
- [ ] 6.2 Update `examples/benchmarking/fasrc_ragas_queries.README.md` (the `status`/`source_hash` field + draft→locked lifecycle) and `docs/docs/benchmarking.md` (the operator maintenance workflow).
- [ ] 6.3 `openspec validate maintain-ragas-goldenset --strict` passes; each PR gate-green with ≥80% diff coverage; no `--no-verify`.
