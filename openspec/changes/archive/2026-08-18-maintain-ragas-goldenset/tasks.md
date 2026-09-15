> **Sequencing:** each numbered group is a separate, gate-green PR. Data backfill (group 1) is
> mechanical and lands before behavior. Detection subcommands (groups 2–4) are read-only and
> TDD'd with injected fetch/LLM + fixture corpus/bank. The cron (group 5) lands last; the skill
> (group 6) is a personal tool and rides **no** repo PR.
>
> **Docs ride the surface they document** (per repo rule: user-facing behavior/CLI flags update
> `docs/` in the **same** change). So each PR that lands a field or an operator-invocable
> subcommand ships its own doc update — the `status`/`source_hashes` field doc rides group 1, and
> each `coverage`/`orphans`/`--propose`/`drift`/`report` surface documents itself in the group
> that introduces it. No group lands user-facing surface without docs.
>
> Every code group is TDD: failing test first → minimum code → refactor; gate green
> (`bash scripts/gate.sh`, ≥80% diff coverage) before commit; branch from `origin/dev`, PR to
> `fasrc/archi:dev`.

## 1. Confirmation state: `status` / `source_hashes` field + backfill (data + schema)

- [x] 1.1 Check the `benchmark_schema` edit seam is black-clean before touching it (black-churn / diff-coverage trap); route through a new helper if not.
- [x] 1.2 TDD: `benchmark_schema` treats a missing `status` as `draft` and preserves `status`/`source_hashes` on load; a `locked` row may carry a `source_hashes` map (one entry per `sources` URL) or, for an empty-`sources` `should_refuse` row, none; assert the harness loads a bank with and without the fields identically (no eligibility/scoring change — `status` is not consulted by scoring).
- [x] 1.3 TDD: a `bank_status_counts(bank)` helper returns `locked`/`draft` counts and the `anchor_type` distribution from the field (not by parsing `notes`).
- [x] 1.4 Backfill `status: draft` into all rows of `examples/benchmarking/fasrc_ragas_queries.json` and `examples/benchmarking/anchor_questions.json`; land as its own commit, separate from behavior.
- [x] 1.5 Assert (test or scripted check) the backfilled bank scores identically to pre-backfill for a fixed fake run — field addition is behavior-neutral.
- [x] 1.6 **Docs (same PR):** update `examples/benchmarking/fasrc_ragas_queries.README.md` for the `status` / `source_hashes` fields and the `draft → locked` lifecycle (incl. the source-less `should_refuse` case and the "scoring is unchanged; `locked`-eligibility is deferred" note).

## 2. Corpus read + coverage/orphan detection (read-only, TDD)

- [x] 2.1 TDD: a read-only corpus accessor returns the ingested URL set with each doc's `source_type`/`parent` labels, via the existing Postgres path (mirror the ingestion-verifier read); injectable/fakeable for hermetic tests. Do NOT rely on the corpus resource hash — `ScrapedResource.get_hash()` is URL-only.
- [x] 2.2 TDD: URL reconciliation **reuses `sitemap_source.normalize_page_url`** (PR #133) on both corpus URLs and row `sources` — same canonical form the ingest stores (scheme/host case, fragment, `/x` vs `/x/` #118), no bespoke normalizer. The residual redirect-alias slug near-miss goes to a separate "needs reconciliation" bucket, never classified as a definitive gap or orphan.
- [x] 2.3 TDD: `coverage` reports corpus URLs referenced by no row's `sources`; empty when every corpus URL is covered; covered-ness is derived from the current bank, not any ledger.
- [x] 2.4 TDD: `orphans` flags rows whose `sources` URL is absent from a **freshly expanded live source inventory** (reuse `sitemap_source.expand_sitemaps` for `sitemap-` sources; the list itself for hand-listed), NOT from corpus-absence alone (the corpus never prunes) — a stale corpus row for a removed URL must still yield an orphan; `should_refuse` rows (empty `sources`) are never flagged; nothing is deleted.
- [x] 2.4a TDD: an **incomplete** inventory (any source document failed to fetch/parse — `expand_sitemaps` fails open — or expansion below its configured floor) is treated as an operational failure that **abstains** from orphan-flagging that run (no false orphans against a partial inventory).
- [x] 2.4b TDD: the live inventory is authoritative only for the hosts it actually contains — a `sources` URL on any other host (an external authority the KB never ingested, e.g. the 18 `slurm.schedmd.com` rows in the FASRC bank) is reported **out of scope**, never as an orphan. Found during 2.4: without this guard the first run false-flags 17% of the bank.
- [x] 2.5 TDD: `coverage` groups and filters gaps by source (`source_type`/`parent`) so a high-volume git source (per-file blob URLs) doesn't flood the report; greenlight per source or path glob.
- [x] 2.6 Wire `coverage` / `orphans` as subcommands of `scripts/benchmarking/goldenset_maintenance.py`, loading the bank via `benchmark_schema`.
- [x] 2.7 **Docs (same PR):** document the `coverage` / `orphans` operator usage (incl. slug near-miss bucket and the live-inventory orphan basis) in `docs/docs/benchmarking.md`.

## 3. Coverage candidate proposal (greenlit-only, TDD)

- [x] 3.1 TDD: `coverage --propose <url>` drafts grounded candidate questions for a greenlit page as `status: draft`, with `sources` set and references left draft (LLM client injected/faked).
- [x] 3.2 TDD: `--propose` never drafts for a non-greenlit page and never locks a proposed candidate.
- [x] 3.3 TDD: proposals are emitted for review only — the bank JSON file is byte-unchanged by a `--propose` run (writing a candidate in is a separate human-applied step).
- [x] 3.4 TDD: the decision ledger records **declines only**; `coverage` suppresses declined URLs but re-derives *covered* from the current bank, so a greenlit-but-unapplied page (candidates drafted, no bank row added) still appears as a gap; declining a page (operator skip) appends to the ledger.
- [x] 3.5 **Docs (same PR):** document `coverage --propose`, the greenlight flow, and the declines-only ledger in `docs/docs/benchmarking.md`.

## 4. Fact-drift detection (hash tripwire → LLM diff, TDD)

- [x] 4.1 TDD: `source_hashes` are content hashes the tool computes over the **normalized extracted text** of **each** re-fetched source URL (reuse the ingest's extraction + `normalize_page_url`, not raw markup — D6 sign-off condition, so formatting-only changes don't false-flag), NOT read from the corpus identifier (URL-only); a `locked` row is flagged when **any** of its `sources` URLs has a fresh hash differing from its stored `source_hashes` entry (naming the changed URL), and not flagged when all match.
- [x] 4.2 TDD: `draft` rows, and `locked` source-less `should_refuse` rows, are never drift-checked, regardless of hash.
- [x] 4.3 TDD: on a hash mismatch the tool asks the injected LLM whether the stored `reference` still holds against the re-fetched source (injected fetch); output is advisory; `reference`/`status` are left unchanged.
- [x] 4.4 Wire `drift` as a subcommand; confirm no detection path writes to the bank file.
- [x] 4.5 **Docs (same PR):** document `drift` (hash tripwire → LLM diff, advisory-only) in `docs/docs/benchmarking.md`.

## 5. Read-only `report` + dev-server cron

- [x] 5.1 TDD: `report` prints coverage gaps, drift flags, and orphans, modifies no file, and exits zero when findings exist — reserving non-zero for operational failure (unreachable corpus).
- [x] 5.2 **Repo mechanism (this PR):** the tracked cron wrapper `scripts/benchmarking/goldenset_report_cron.sh` — read-only, writing only to `.ralph/log/` — with a hermetic self-test, an env-file config (crontab has no line continuation), a one-line crontab entry, and documented install + rollback (remove the line). This delivers the *means* to run `report` nightly; it does **not** install anything.
- [ ] 5.2a **Deploy (tracked follow-up, not a repo change): install + end-to-end validate the cron on fasrc-dev** — tracked in fasrc/archi#148. No repo PR can create or validate a crontab, resolve the live DSN/source list, or exercise cron's minimal `PATH` and the mail digest on a machine it does not touch; those are machine facts verifiable only on the target. This box stays open until #148 confirms a live hand-run exits zero, all three passes complete against the live corpus, and the bank is left byte-unchanged.
- [x] 5.3 **Docs (same PR):** document the `report` subcommand + cron install/rollback in `docs/docs/benchmarking.md`.
- [x] 5.4 The confirmation census (task 1.3's `bank_status_counts`) is printed by `report` — the spec's "WHEN the tool reports on the bank" scenario had a helper but no CLI surface until now.
- [x] 5.5 `scripts/gate.sh` runs the wrapper's shell self-test, so a contract that lives entirely in bash cannot break with every required check still green.

## 6. Skill (personal tool — no repo PR)

- [x] 6.1 Author `~/.claude/skills/archi-ragas-goldenset/SKILL.md` (personal tool, NOT in this repo PR): conversational `coverage` mode (present newly-uncovered pages grouped by source, draft only the operator's greenlit picks in plain-language reply, record skips to the decision ledger), plus `drift-confirm` / `report` modes; every mutation gated on human confirmation (add draft, lock reference, prune orphan as explicit apply steps).
- [x] 6.2 `openspec validate maintain-ragas-goldenset --strict` passes; each PR gate-green with ≥80% diff coverage; no `--no-verify`. (Per-surface docs land with their own group — 1.6 / 2.7 / 3.5 / 4.5 / 5.3 — not deferred here.)
