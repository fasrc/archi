# Read the RAGAS golden set directly in the QA evaluation console

## Why

FASRC maintains one curated question bank —
`config/benchmarking/fasrc_ragas_queries.json`, 105 rows, plus the 5-row
`examples/benchmarking/anchor_questions.json`. Every row carries the same seven
fields: `user_input`, `reference`, `sources`, `source_match_field`, `anchor_type`,
`status`, `notes`.

The RAGAS benchmark reads it. The QA evaluation console cannot, and the console is
the surface the release plan calls the operator interface for the automated eval
gate (#320). So the same questions have to be curated twice, in two files, by hand
— and the second copy starts drifting the day it is created.

The console refuses the bank twice over, and only the first refusal is visible:

```
ValueError: dataset row 0 has unknown field(s): notes, sources
```

That is `_strict_row_keys` (`src/evaluation/qa/dataset.py:488`) rejecting anything
outside `COMMON_ITEM_FIELDS` (`:26-36`). Deleting that check is not enough,
because the second wall is structural: `DatasetItem` is a frozen dataclass with a
fixed field list (`:68-79`) and `dataset_item_to_dict` rebuilds output from those
fields alone (`:737-757`). A field that survives validation still evaporates at
the model boundary.

That matters because the console **writes** datasets, it does not only read them:
reviewing generated atoms saves an immutable child dataset. So a permissive
validator with no carry-through would accept the bank, and then emit a child with
`sources` and `status` silently gone — the exact moment an operator did useful
work is the moment the one file forks into two. A loud refusal is better than
that; neither is what we want.

**The rest of the system already settled this argument in the other direction.**
`align-ragas-benchmark-dialect` deliberately standardized the bank on ragas
0.3.5's native dialect (`user_input`/`reference`) so the external
`fasrc/ragas-json-editor` tool could author it, and added a normalize-on-read shim
for legacy files rather than forcing a rewrite. `2026-08-18-maintain-ragas-goldenset`
then added `status` and `source_hashes` to the bank, resting explicitly on the fact
that "the harness requires only `user_input` and already tolerates extra fields."
Two accepted changes treat the bank as a canonical artifact that grows additively
and is read through adapters. The console is the one reader that does not play
along.

## What Changes

- **A carry-through slot on the dataset model.** `DatasetItem` gains
  `extra: Optional[Dict[str, Any]]`, populated at parse from row keys outside the
  known set and re-emitted by `dataset_item_to_dict`. Extras are data in transit,
  not fields the console interprets: nothing in preparation, running or scoring
  reads them. This is what makes a reviewed child dataset still a valid RAGAS bank.
- **`extra` participates in content addressing.** `canonical_json`
  (`src/evaluation/qa/dataset.py:193`) feeds the `sha256` the catalog dedupes on
  (`catalog.py:_sha256`), so two banks differing only in `sources` must not collide
  as the same dataset. A test pins that.
- **Strict key checking stays on, narrowed rather than removed.** Unknown keys stop
  being an error and become carried extras, but the *known* names keep their
  meaning: a row is still rejected when a known field has the wrong type or a
  required one is missing. The reason to keep this narrow is `expected_atoms` —
  misspell it and the field is absent, and absent means "invoke the atom
  extractor," so a reviewer's approved obligations would be silently replaced by
  LLM-inferred ones on a run that reports success. Near-miss known names
  (edit distance 1 from a known field) are therefore refused rather than carried,
  so a typo cannot hide inside `extra`.
- **A dialect adapter at the import boundary.** `EvaluationCatalog.import_dataset`
  (`src/evaluation/qa/catalog.py:448`) already receives the raw bytes before
  validation. It gains a normalize step that recognizes the ragas dialect by the
  presence of `user_input` and maps `user_input→question`, `reference→answer`,
  synthesizes a stable `id` when absent, and defaults `time_sensitive` to `false`.
  The mapping direction mirrors the shim `align-ragas-benchmark-dialect` already
  established, and lives at the boundary so no dialect knowledge leaks into the
  parser.
- **The adapter reports, never guesses.** The import result names which dialect was
  detected and which keys were carried as extras. Silent success on a
  misunderstood file is the failure mode this whole change exists to avoid.

## Impact

- Affected code: `src/evaluation/qa/dataset.py` (model + parse + serialize +
  canonical hashing), `src/evaluation/qa/catalog.py` (import adapter), and their
  unit tests. No config, no template, no deployment change.
- Affected capability: `qa-evaluation-trial` gains dataset-ingestion requirements.
  `ragas-goldenset-maintenance` is untouched — the bank's shape does not change,
  which is the point.
- The 105-row bank becomes usable in the console as-is, which closes the console's
  standing "0 datasets" state with curated FASRC content instead of authored-for-
  the-purpose filler.
- **Out of scope, deliberately:** `sources` and `source_match_field` are carried but
  not scored. The console evaluates gold atoms; source retrieval remains a
  RAGAS-side metric. One file can feed both stacks without the two stacks measuring
  the same thing, and this change does not pretend otherwise.
- **Also out of scope:** writing reviewed atoms back into the bank. Caching
  `expected_atoms` in the canonical file would make runs cheaper and reproducible,
  but it means the console mutating a git-tracked artifact the benchmark depends
  on, which deserves its own change and its own argument.
