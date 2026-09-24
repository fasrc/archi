## 1. Shared attribution (`scripts/benchmarking/category_attribution.py`)

- [ ] 1.1 `model: opus` — RED: tests — first-source owner; cross-category line; uncategorized first source not moved; unresolved URL and two-category URL reported; coverage over every source; row share over every citation (the 20 % second-source case); per-metric power (the cross-category-only article case)
- [ ] 1.2 `model: opus` — GREEN: implement the module (pure, no I/O)

## 2. Census script (`scripts/benchmarking/category_census.py`)

- [ ] 2.1 `model: sonnet` — RED/GREEN: coverage census on stubbed rows — deleted rows excluded by the query, 90 % gate
- [ ] 2.2 `model: sonnet` — RED/GREEN: routing-list parser for `## Category routing`; drift census names prompt-only and corpus-only labels; non-KB categories excluded
- [ ] 2.3 `model: sonnet` — RED/GREEN: bank census — ≥ 6 categories gate, > 10 % share gate, the side lines, no row dropped
- [ ] 2.4 `model: sonnet` — RED/GREEN: exemplar parser and disjointness — clean r0b fixture passes; exact, paraphrase and shared-URL collisions fail with names
- [ ] 2.5 `model: sonnet` — RED/GREEN: CLI — `--pg-dsn`, `--bank`, `--anchors`, `--routing-prompt`, `--exemplar-prompt`, `--similarity-threshold`, `--json`; corpus fingerprint in the output; exit codes 0 / 1 / 2

## 3. Fallback pin

- [ ] 3.1 `model: sonnet` — test for `_build_extra_text` and `search_metadata`'s `extra_text ILIKE` fallback with a capturing stub connection (passes on first run: it pins existing behavior, so verify it fails when the fallback line is changed locally, then revert)

## 4. Verify

- [ ] 4.1 `model: sonnet` — `bash scripts/gate.sh` green; `openspec validate preflight-category-census --strict`
