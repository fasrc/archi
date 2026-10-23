## Context

Issue #731 is PR 2 of `openspec/changes/offload-ragas-judge-to-slurm/` (tasks 2.1–2.6
with 2.2a, 2.2b and 2.5a; design D3, D3a, D8a and D10). That change is the design of
record. This document records the facts that make PR 2 safe, measured on `origin/dev`
`e2978a1d` (2026-10-23, after #738 merged #730), and closes the gaps that the parent
leaves open. The issue body anchors are at `7c7d7450`. These are the current anchors
(`src/bin/service_benchmark.py` unless named):

- `ResultHandler.handle_results` `:630`. It builds `current_results` and appends it to
  `ResultHandler.results` at `:859`. The #518 null judge fields are
  `ragas_effective_settings` `:815-826` and `ragas_judge_identity_effective` `:834-836`,
  both `null` when `ragas_scored is False`.
- `ResultHandler.add_metadata` `:863`. It computes `code_version` only there, at the end
  of the run: `collect_code_version(PACKAGE_DIR, additional_info)` `:915`, with
  `additional_info` read from `EXTRA_METADATA_PATH` (`:80`) at `:865-870`.
  `PACKAGE_DIR` is `:87`.
- `ResultHandler.dump_artifacts` `:934`. It picks the timestamp at `:943` and passes it
  to `dump_category_maps` (`:944`, `:960-982`), `dump` (`:945`, file name `:1007`) and
  `dump_report` (`:947`, file name `:996`).
- `Benchmarker.__init__` `:1695`: `self.all_config_files = self.get_all_configs(configs)`
  `:1699`, then `"FINISHED"` is appended `:1700`, then `load_new_configuration()` pops
  the first config (`:1706`, `:1717-1718`).
- `Benchmarker.get_ragas_results` `:2131`. It reads `enabled_metrics` from
  `self.benchmarking_configs` `:2150`, `ragas_settings` from `self.config` `:2153`, and
  `verbosity` from `self.config["global"]` `:2156`, then calls `score_ragas`.
- `Benchmarker._process_config` `:2194`. It collects `ragas_input` from each question's
  `dataset_result` `:2241-2242`. In the RAGAS block `:2253-2290` it sets
  `self._ragas_scored = False` `:2258`, then either calls `get_ragas_results` with
  `list(scorable_items(question_wise_results).keys())` `:2265-2268`, or, with no input,
  writes `n/a` aggregates `:2269-2290`.
- `_answer_and_score_question` `:2308` builds the ragas record `:2394-2399`:
  `{user_input, retrieved_contexts, response, reference}` with full contexts.
- `Benchmarker.run` `:2423`: the arm loop `:2444-2482` resets `_judge_usage`,
  `_ragas_judge_identity` and `_ragas_scored` at `:2451-2453`, calls `_process_config`
  `:2454`, `handle_results` `:2455`, then `load_new_configuration` `:2482`.
  `dump_artifacts(self.benchmark_name)` is the last call, `:2615`.
- `src/utils/ragas_scoring.py` `score_ragas` `:152`: `batch_size =
  ragas_settings["batch_size"] or None` `:180`, `RunConfig(**ragas_run_config_kwargs(
  ragas_settings, verbosity))` `:181`, metrics `[name for name in RAGAS_METRIC_NAMES if
  name in enabled_metrics]`.
- `src/utils/benchmark_provenance.py`: `code_fingerprint` `:577`, `code_version` `:673`,
  `collect_code_version` `:702` (returns `digest: None` and an `<unavailable: ...>`
  `source` on failure, never raises).
- `src/cli/templates/base-config.yaml` `:90-129` renders `ragas_settings`.
  `evaluator_provider_mode` `:123-129` is the precedent for a key that renders only when
  it is set, with `| tojson`.
- `OUTPUT_DIR` is `/root/archi/benchmarks` (`:79`, `:88`), bind-mounted from the host
  `out_dir` (`src/cli/templates/base-compose.yaml:827`).
- `arm_divergence_refusal` is at `src/cli/tools/config_seed.py:130` (the issue says
  `src/utils/config_seed.py`; that file does not exist).
- `tests/unit/test_benchmark_resilience.py:519` and `:571` replace `dump_artifacts` with
  `lambda name: None`, and their `_RunStub` replaces `__init__`, so `run()` there has no
  `judge_plan`.

## Goals / Non-Goals

**Goals:** the acceptance criteria of #731: `judge_mode` parsing and the one-mode rule,
the rendered keys, the fixed result basename, the start refusals, the bundle writer, the
pending arm, the `inline` default for old results, the `link` primitive, and the publish
script. An inline run gives the same result JSON as before, with no new keys.

**Non-Goals:** the scorer, claims, checkpoints, `judge_identity` and the merge (#732);
Slurm scripts, gates and docs (#733); the Lynx scorer (#734); task 2.7 and
`publish.py --status` (operator step on #729); building the judge or the embeddings in a
deferred run.

## Decisions

### D1. This folder is a driver; the parent change is the design of record

The nightly supervisor reads its tasks from
`openspec/changes/fix-issue-<n>-<slug>/tasks.md` and checks the branch name
`fix/issue-<n>-<slug>`. Issue #731 forbids a new design and names the branch
`feat/judge-deferred-bundle`. As a result, this change holds the task list and the
requirements that pin PR 2, and the PR uses `fix/issue-731-judge-deferred-bundle`. The
spec delta goes to the existing capability `retrieval-benchmarking`, not to the parent's
new `offline-judge-scoring`, so the two changes cannot collide at archive time (the #730
driver did the same). The last task corrects the parent's design and spec to agree with
D2–D11 below.

### D2. Three new keys, rendered only when set

`base-config.yaml` gets three keys in `ragas_settings`, each in the shape of
`evaluator_provider_mode` (`:123-129`): rendered only when the value is defined and not
none, and always through `| tojson`, so that a configured scalar keeps its type.

| Key | Meaning | Absent |
|---|---|---|
| `judge_mode` | `inline` or `deferred` | `inline` |
| `judge_bundle_dir` | bundle folder, relative to the benchmark output folder | `judge_bundles` |
| `evaluator_model_revision` | the judge revision that a deferred bundle asks for | none |

An inline config therefore renders byte for byte as before, so `configuration` and
`config_version` in an inline result do not change.

`parse_judge_mode(ragas_settings)` returns `"inline"` when the key is absent, `"inline"`
for `"inline"`, `"deferred"` for `"deferred"`, and raises `ValueError` for any other
value, including `""`, `false`, `null` that reached the file, and other case forms. The
message names the value and the two allowed values.

**Why `evaluator_model_revision`.** The parent spec makes the manifest record "the judge
model id and revision", and D4a of the parent matches a bundle to a judge job by both
(`judge.env` `JUDGE_MODEL_ID` and `JUDGE_MODEL_REVISION`, #732). Nothing in a config
names a revision today. The judge model id is the existing `evaluator_model` (the RAGAS
judge model, `base-config.yaml:121`). The revision is its sibling key. A deferred run
does not use `evaluator_provider`: no judge is built.

### D3. One start check, in `Benchmarker.__init__`, before any question

`plan_judge_run(config_paths, *, package_dir, git_info_path, package_digest_fn=...)` in
`src/utils/judge_bundle.py` returns a small frozen `JudgePlan` (`mode`, `arm_count`, and
for a deferred run `bundle_dir`, `code_version` and `package_digest`), or raises `JudgeRunRefused`
(a `ValueError` subclass). `Benchmarker.__init__` calls it on
`self.all_config_files` right after `get_all_configs` (`:1699`), before `"FINISHED"` is
appended and before `load_new_configuration`, and stores `self.judge_plan`. A raise
there stops the container before the ingest wait and before any question.

The function, in this order:

1. Reads each config file with `yaml.safe_load`, takes
   `services.benchmarking.mode_settings.ragas_settings` (missing levels read as `{}`),
   and parses `judge_mode`. An invalid value refuses, and names the config path.
2. If the configs do not all have the same mode, refuses, and names every config with
   its mode (design D8a; a missing key and `inline` are the same mode).
3. With every config `inline`, returns `JudgePlan(mode="inline", arm_count=n)` and
   computes no digest. An inline run therefore does no new work at start.
4. With every config `deferred`, refuses, and names the config, when `evaluator_model`
   or `evaluator_model_revision` is empty or not a string, or when `judge_bundle_dir`
   is not a non-empty relative path with no `..` part. A bundle outside the output
   folder is outside the bind mount (`base-compose.yaml:827`) and is lost with the
   container. Then it resolves each config's `judge_bundle_dir` (an absent key is
   `judge_bundles`) and compares the values as `PurePosixPath(value).parts`, so `"x"`,
   `"x/"` and `"./x"` are the same folder. If they differ, it refuses, and names every
   config with its resolved value. The reason is the D8a one: publish (D11) scans one
   bundle folder, and the merge waits for every arm, so a result split across two
   folders would never be merged.
5. Computes `code_version = collect_code_version(package_dir, git_info)`, where
   `git_info` is read from `git_info_path` with the tolerant read of `add_metadata`
   (`:865-870`; an `OSError` gives `None`). If `code_version["digest"]` is `None`,
   refuses, and quotes `code_version["source"]` (parent D10: no scorer could ever match
   the bundles).
6. Computes the package digest (D7). If it is unavailable, refuses, and quotes the
   reason, for the same reason as step 5.
7. Returns `JudgePlan(mode="deferred", arm_count=n, bundle_dir=<the one resolved
   value>, code_version=..., package_digest=...)`.

`arm_count` is the number of config paths, which equals the number of arms that `run()`
records (`run()` loops once for each config).

Code that runs without `__init__` (the `_RunStub` tests, `object.__new__` benches) reads
`getattr(self, "judge_plan", None)`. `None` means inline, so those tests keep today's
path.

### D4. The result basename is fixed at the top of `run()`, for every run

`ResultHandler.result_timestamp()` returns
`datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")` (the format of `:943`).
`run()` calls it once, as its first statement, and sets
`self.result_basename = f"{self.benchmark_name}-{timestamp}"`. At the end, `run()`
calls `ResultHandler.dump_artifacts(self.benchmark_name, timestamp=timestamp)`.
`dump_artifacts(benchmark_name, timestamp=None)` uses the given timestamp for all three
writers, and picks a fresh one only when it gets `None`, so other callers and the
existing `dump_artifacts` tests keep working. The JSON and its `_report.md` still share
one timestamp, so the backfill invariant holds.

This applies to inline runs too, so there is one code path. The only visible change for
an inline run is that the file name records the start of the run, not its end.
`metadata.time` still records the end. Runs that follow each other still sort in run
order by name.

The two stubs at `tests/unit/test_benchmark_resilience.py:519` and `:571` change from
`lambda name: None` to `lambda name, **kwargs: None`. No other line of that file
changes.

### D5. The bundle writer

`write_judge_bundle(bundle_root, *, result_basename, arm_index, arm_count, rows, keys,
enabled_metrics, ragas_settings, verbosity, code_version, package_digest)` in
`src/utils/judge_bundle.py` writes `bundle_root / f"{result_basename}-arm{arm_index}"`
and returns `{"folder": <folder name>, "digest": <rows_sha256>}`.

- `arm_index` is 0-based: the arm's position in `benchmarking_results` (the parent spec
  scenario names "the bundle of arm 0").
- It creates `bundle_root` if needed, and creates the arm folder with `mkdir()` (no
  `exist_ok`). An existing folder raises, and the run stops. It never overwrites a
  bundle.
- `len(rows) != len(keys)` raises `ValueError`.
- `rows.jsonl`: one line for each `(key, row)` pair, in order:
  `{"key", "user_input", "retrieved_contexts", "response", "reference"}`, built from
  exactly those four row fields (a missing field raises `KeyError`). Each line is
  `json.dumps(record, sort_keys=True, allow_nan=False)` plus `"\n"`, encoded as UTF-8.
  The default `ensure_ascii=True` escapes every non-ASCII character, so a lone
  surrogate in an answer or a context (which the inline path passes to ragas without a
  problem) cannot make the UTF-8 encode raise. `json.loads` gives back the same strings.
  Zero rows give an empty file (D6).
- `rows_sha256` is `"sha256:" + sha256(<rows.jsonl bytes>).hexdigest()`, the
  `sha256:<hex>` form of `code_fingerprint`.
- `manifest.json`: `json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False)`
  plus `"\n"`, with exactly these keys:

  | Key | Value |
  |---|---|
  | `bundle_format` | `1` |
  | `result_basename` | the fixed basename (D4) |
  | `arm_index` | 0-based |
  | `arm_count` | `JudgePlan.arm_count` |
  | `row_count` | `len(rows)` |
  | `rows_sha256` | as above |
  | `enabled_metrics` | `[m for m in RAGAS_METRIC_NAMES if m in enabled_metrics]` |
  | `ragas_run_config_kwargs` | `ragas_run_config_kwargs(ragas_settings, verbosity)` |
  | `batch_size` | `ragas_settings.get("batch_size") or None` |
  | `embedding_model` | `ragas_settings.get("embedding_model")` |
  | `judge_model` | `{"id": evaluator_model, "revision": evaluator_model_revision}` |
  | `code_version` | the whole `JudgePlan.code_version` block |
  | `package_digest` | `JudgePlan.package_digest` |

  `enabled_metrics`, `ragas_run_config_kwargs` and `batch_size` are the same
  expressions that `score_ragas` uses (`ragas_scoring.py:180-181`), so the offline
  scorer can pass the run's values to ragas.
- Write order: `rows.jsonl`, then `manifest.json`, then an empty `BUNDLE_COMPLETE`.
  Each file is written, flushed and `fsync`ed before the next one is created, so
  `BUNDLE_COMPLETE` exists only after both other files are complete.

The bundle digest that the arm records is `rows_sha256`. The manifest holds it, so
publish (D10) and the merge (#732) can check the rows against both.

### D6. The deferred call site in `_process_config`

In the RAGAS block (`:2253`), after `self._ragas_scored = False`, a deferred plan takes a
new branch before `if ragas_input:`. The branch:

- calls `write_judge_bundle` with `bundle_root = OUTPUT_DIR / self.judge_plan.bundle_dir`
  (one value for the whole run, D3 step 4), `rows = ragas_input`,
  `keys = list(scorable_items(question_wise_results).keys())`, this arm's
  `enabled_metrics`, `ragas_settings` and `verbosity` from the same sources as
  `get_ragas_results` (`:2150-2156`), `self.result_basename`, `self._arm_index`, and
  the plan's `arm_count`, `code_version` and `package_digest`;
- stores the return value in `self._judge_bundle`;
- adds nothing to `total_results`: no `aggregate_<metric>`, no `<metric>_scored`, and
  no `n/a` block.

It never calls `get_ragas_results`, `get_ragas_llm_evaluator` or
`get_ragas_embedding_model`, so no judge and no embedding object is built.
`self._ragas_scored` stays `False`, so `handle_results` writes the #518 null judge
fields. An arm with zero scorable rows still writes a bundle with an empty `rows.jsonl`:
the merge (#732, parent D8) waits for a bundle from every arm, so a missing bundle would
block the result for good.

`run()` sets `self._judge_bundle = None` with the other resets (`:2451-2453`), sets
`self._arm_index` to the loop count before `_process_config`, and passes
`judge_bundle=getattr(self, "_judge_bundle", None)` to `handle_results`. The logic lives
in `judge_bundle.py`. The `service_benchmark.py` lines are thin call sites.

The A/B pairing and the leaderboard in `run()` (`:2486-2521`) already read aggregates
with `.get` (`build_leaderboard` `:1317`; `pair_ab_results` compares only the metrics
both arms have, `:1078`), so a pending arm gives `None` metrics there, not a crash. The
merge (#732) rebuilds both from the judged arms. Both blocks run only when
`ResultHandler.results` holds 2 or more records (`:2489`, `:2509`), so the task 5.1 test
puts two pending records there.

### D7. The package digest helper

In `src/utils/benchmark_provenance.py`, beside `code_fingerprint`:

- `package_digest(freeze_text)`: strip each line, drop empty lines, sort, join with
  `"\n"`, and return `"sha256:" + sha256(...).hexdigest()`.
- `collect_package_digest(run=subprocess.run)`: runs
  `[sys.executable, "-m", "pip", "freeze"]` with `capture_output=True, text=True,
  timeout=120, check=False`, and returns `{"digest": ..., "source": "pip freeze"}`. A
  non-zero exit, an `OSError`, a timeout or empty output gives `{"digest": None,
  "source": "<unavailable: ...>"}` with the reason. It never raises, like
  `collect_code_version`.

#732 calls the same function for the scorer identity, so both sides hash the same way.

### D8. The pending arm record

`handle_results` gets a keyword `judge_bundle: Optional[Dict[str, str]] = None`. When it
is not `None`, `current_results` gets two more keys before the append (`:859`):
`judge_status: "pending"` and `judge_bundle: {"folder": ..., "digest": ...}`. When it is
`None`, nothing is added, so an inline arm has no new key.

`judge_status_of(arm_record)` in `judge_bundle.py` returns
`arm_record.get("judge_status") or "inline"`. A result written before this change
therefore reads as `inline`. publish (D10) uses it, and the G8 gate (#733) will.

### D9. `RESULT_COMPLETE`

After `dump_artifacts` returns, a deferred run (`judge_plan.mode == "deferred"`) creates
an empty `OUTPUT_DIR / f"{self.result_basename}.RESULT_COMPLETE"` through
`write_result_complete(output_dir, result_basename)` in `judge_bundle.py`. An inline run
writes no marker. If `dump` raises, the marker is never written. (`dump_artifacts`
swallows a `_report.md` failure, so a report failure does not hold back the marker. The
JSON is the source of truth.)

### D10. The exclusive-link primitive

`src/utils/exclusive_link.py`, standard library only, with no import from `src`. #732's
`queue.py` must run on a login node, so it can load this file by path.

`link_exclusive(path, data) -> bool` creates `path` with the bytes `data` and never
replaces an existing `path`:

1. Create a unique temporary file in the same folder,
   `.tmp.<host>.<pid>.<secrets.token_hex(8)>`, with `O_CREAT | O_EXCL | O_WRONLY`.
2. Write all of `data`, `flush`, `os.fsync`, close.
3. `os.link(tmp, path)`. On success, return `True`.
4. On `FileExistsError`, return `True` if `os.stat(tmp).st_nlink == 2` (an NFS resend of
   a link that succeeded), else `False`.
5. In a `finally`, remove the temporary file (ignore `FileNotFoundError`).

A `False` return means that another writer owns `path`. The caller compares bytes if it
needs to.

### D11. `scripts/benchmarking/judge/publish.py`

`python scripts/benchmarking/judge/publish.py --out-dir <dir> --queue <dir>
[--bundle-dir judge_bundles]`. `--queue` is required, because the queue location is an
open operator question (#729). The script puts the repository root on `sys.path` like
`scripts/benchmarking/feature_matrix/sweep_tools.py:37-38`, and imports
`link_exclusive` and `judge_status_of`. Each step is idempotent. A conflict is reported
on stderr, the script continues with the other bundles, and it exits 1 at the end. With
no conflict it exits 0, also when there is nothing to do.

1. **Bundles.** For each folder `B` under `<out-dir>/<bundle-dir>/` that has
   `BUNDLE_COMPLETE`: read `B/manifest.json`; the queue folder is `Q = <queue>/<B.name>`.
   A folder name that is not `f"{result_basename}-arm{arm_index}"` is a conflict.
   - If `Q/READY` exists: the same `rows_sha256` in `Q/manifest.json` is a skip; a
     different one is a conflict, and nothing changes.
   - Else: copy `rows.jsonl` and `manifest.json` into `Q` (each through a temporary file
     and `os.replace`, because a folder with no `READY` is ignored by every reader), then
     check that the copied `rows.jsonl` hashes to the manifest's `rows_sha256` and that
     the copied manifest equals the source bytes. A mismatch is a conflict, with no
     `READY`. Then create `Q/READY` with `link_exclusive(Q / "READY", b"")`. A `False`
     return (another publisher won) falls back to the `READY` check above.
   - A folder with no `BUNDLE_COMPLETE` is not copied.
2. **Pending result.** For each result basename from step 1: if
   `<out-dir>/<basename>.RESULT_COMPLETE` does not exist, or
   `<out-dir>/<basename>.json` does not load as JSON, skip it (a later tick retries).
   Else step 1 must have found a complete bundle for every arm index from 0 to
   `arm_count - 1`. Every bundle is written before the result JSON, so a missing arm
   after `RESULT_COMPLETE` is a conflict that names the missing arms, and the result is
   not copied. The file must have `arm_count` entries in `benchmarking_results`, and for
   each bundle of that result, the entry at its `arm_index` must have
   `judge_status_of(...) == "pending"` and `judge_bundle.digest == rows_sha256`. A
   mismatch is a conflict. Then create `<queue>/results/<basename>.json` with
   `link_exclusive` and the file's bytes. If it exists with the same bytes, nothing
   changes; with different bytes, it is a conflict.
3. **Judged copy-back.** For each result basename from step 1: if
   `<queue>/results/<basename>.judged.json` exists, and each of the `arm_count` queue
   folders `<basename>-arm<i>` has `MERGED`, create `<out-dir>/<basename>.judged.json`
   with `link_exclusive` and the judged bytes. Same bytes already there: nothing
   changes. Different bytes: a conflict.

`run_goldenset_eval.sh` does not call the script.

## Risks / Trade-offs

- [A deferred run cannot compute a digest on the dev host, and every deferred run is
  refused] → The refusal names the reason (D3 steps 5–6). Inline runs compute nothing
  new.
- [An inline result file name moves from end time to start time, and a tool depends on
  end time] → `metadata.time` keeps the end time. The backfill invariant depends only
  on the JSON and the report sharing one timestamp.
- [A test changes `ResultHandler` class state (results, metadata) and leaks into another
  test] → New tests patch `ResultHandler.results`, `metadata` and `OUTPUT_DIR` with
  `monkeypatch`, as the existing `test_benchmark_dump_artifacts.py` fixture does.
- [A test passes on zero files: a glob over TSVs that the fixture never writes, or an
  A/B block that is skipped with fewer than 2 records] → The tasks name the exact files
  and the record counts that each test must create and assert.
- [The two-writer race test hangs pytest] → Use a thread pool with a bounded
  `result(timeout=10)` for each future, and no barrier under a lock.
  `pytest-timeout` is not installed.
- [diff-cover misses the `__init__` call site, because unit tests skip `__init__`] →
  The logic is in `judge_bundle.py`, with full tests. The call site is one statement.
- [A reviewer reads the driver folder as a second design] → D1 and the proposal say that
  the parent is the design of record. The PR body says it again.

## Migration Plan

One PR into `dev`. Config is additive (three optional keys). Old result JSONs read as
`inline`. No deploy step: a deferred run needs the new image, and the switch of the
golden-set config to `deferred` is operator task 5.4 of the parent. Revert the PR, or
remove `judge_mode`, to roll back.

## Open Questions

None that block the PR. The queue location (#729) is a required argument of
`publish.py`. The judge model and its revision come from the deferred config (D2).
