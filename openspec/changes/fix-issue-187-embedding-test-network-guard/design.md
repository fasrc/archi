## Context

`tests/unit/test_ingestion_pipeline_isolation.py` contains two tests that construct a real
embedding model:

```
116     def test_embedding_model_works(self):
128         try:
129             from langchain_huggingface import HuggingFaceEmbeddings
131             model = HuggingFaceEmbeddings(
132                 model_name="sentence-transformers/all-MiniLM-L6-v2"
133             )
...
153         except ImportError:
154             pytest.skip("langchain_huggingface not installed")
```

and the same shape at `:217-278` for `test_embedding_performance_realistic`. Constructing
`HuggingFaceEmbeddings` downloads ~90 MB of weights from the HuggingFace CDN on a cache miss. The
`except ImportError` guard catches a missing *library*; a `ConnectionError` raised while fetching
*weights* is not an `ImportError`, so it escapes and fails the test.

How these tests reach a pull request:

- `scripts/gate.sh:146` — `python -m pytest tests/unit/ --cov=src ...`. An **explicit path
  argument**, not a `testpaths` lookup.
- `pyproject.toml:73-75` — `testpaths = ["tests/unit"]`, `addopts = "-v --tb=short"`. No markers
  are registered and no marker is used anywhere in `tests/unit/` today.
- `scripts/gate.sh` is invoked by the pre-commit hook and by CI (gate.sh header, lines 4-6), so
  the gate is the single chokepoint. Anything outside `tests/unit/` is outside every gating path.

Two constraints shape the solution space. The unattended nightly drain may not edit
`.github/workflows/**`, `scripts/gate.sh`, or any control-plane file — so the fix must work by
moving code and editing `tests/`, `pyproject.toml`, and `docs/` only. And the issue's acceptance
criteria demand *both* that a network outage leave the job green **and** that the tests report as
"skipped with a reason naming the network" rather than silently passing.

`tests/smoke/` already exists as the home for deliberate, infrastructure-dependent tests — docker
compose fixtures, a live-deployment smoke runner (`docs/docs/developer_guide.md:230`), RAGAS and
React smoke scripts. It is not in `testpaths` and not in the gate.

## Goals / Non-Goals

**Goals:**

- A HuggingFace CDN outage can no longer red a pull request that touches no ingestion code.
- The `unit-tests` job stops carrying a 90 MB download and a 30-50s-per-file benchmark.
- When the benchmarks are run deliberately and the network is unavailable, they skip with a reason
  that **names the network**, so a permanently broken embedding path cannot hide behind green.
- When the network is available, both tests still execute and still assert
  (`len(embeddings[0]) == 384`). They must not become permanent skips.
- The guard names specific exception types. A bare `except Exception` would make these tests
  incapable of ever failing.

**Non-Goals:**

- Deleting the tests. The embedding path is worth exercising; it moves, it does not disappear.
- Pre-caching weights in CI (issue option (c)). That edits `.github/workflows/**`, which this
  change may not touch — see D3.
- Rewriting what the benchmarks measure, their timing printouts, or their thresholds.
- Adding a mocked unit-level embedding test to replace the gating coverage. The relocated tests
  never covered `src/` (task 1.3 verifies), so there is no coverage hole to backfill, and
  inventing one would be inventing a requirement the issue does not state.
- Registering a pytest marker system for the repo. See D2 — not needed here, and a marker plus
  `addopts` deselection is a broader change than relocation.

## Decisions

### D1: Do both (a) and (b), because (a) alone does not fix the wall-clock cost

The issue offers (a) broaden the guard, (b) move them out of the gating suite, (c) pre-cache in CI.
Neither (a) nor (b) alone satisfies the stated acceptance criteria:

- **(a) alone leaves the 18-minute retry storm intact.** The `ConnectionError` only surfaces
  *after* the HuggingFace client exhausts its internal retries — that retry storm is what took the
  job from 4m17s to 18m11s. Converting the eventual failure into a skip turns a red 18-minute job
  into a green 18-minute job. The false red goes away; the cost does not. (a) alone also keeps a
  public-internet dependency and a 30-50s benchmark in the suite that gates every PR.
- **(b) alone fails acceptance criterion 2.** A relocated test is *deselected* from the gating run,
  not "skipped with a reason naming the network". And when someone deliberately runs the
  benchmarks during an outage, an unguarded test still fails red — the original defect, just
  relocated with it.

Together they are complementary and each covers the other's gap: (b) takes the CDN and the
benchmark out of the gating path, which is what actually restores the 4-minute job; (a) makes the
deliberate run honest wherever it happens.

### D2: Relocate to `tests/smoke/`; do not introduce a pytest marker

Relocation needs **no configuration change at all**: the gate passes `tests/unit/` explicitly
(`scripts/gate.sh:146`) and `testpaths` is `["tests/unit"]` (`pyproject.toml:74`), so a file under
`tests/smoke/` is invisible to both the gate and a bare `pytest`. `tests/smoke/` is the
established home for exactly this category — tests that need real infrastructure and are run on
purpose.

The alternative — register a `network` marker and add `-m "not network"` to `addopts` — was
rejected. It leaves the CDN dependency inside `tests/unit/`, where the next person adding a test
to that file inherits the ambiguity of a directory whose name no longer means "hermetic". It also
changes the default behaviour of every `pytest` invocation in the repo, including developers'
ad-hoc runs, to fix a two-test problem. Relocation states the same fact structurally: these live
where the other infrastructure-dependent tests live.

Consequence to accept openly: nothing runs `tests/smoke/` automatically, so these benchmarks now
run only when a human or a future CI job asks for them. That is the honest position for a CPU
benchmark that takes 30-50s per file, and D5 makes it discoverable rather than lost.

### D3: Option (c) is out of scope, and the reason is authority, not merit

Pre-caching the model in CI would make the tests offline-deterministic and is arguably the best
long-term answer for keeping them in the gate. It edits `.github/workflows/**`, which the
unattended nightly drain is forbidden to touch, so it cannot be done here. It is not foreclosed:
if a human later wants the benchmarks gating again, a warm HF cache plus this change's guard is
the combination that makes that safe. Record this in the PR body so the option is not silently
lost.

### D4: Name the exception types empirically — and beware that the simulation differs from the outage

The guard must name specific types. The trap: **`HF_HUB_OFFLINE=1` and a real CDN outage raise
different exceptions.**

- The real 2026-08-02 failure was `ConnectionError: Network error: Request middleware error: error
  sending request for url (https://cas-server.xethub.hf.co/...)`.
- `HF_HUB_OFFLINE=1` with a cold cache raises `huggingface_hub` offline/local-entry errors
  (`LocalEntryNotFoundError` / `OfflineModeIsEnabled`), never a `ConnectionError`.

A guard written against only the simulation would still red a PR during a genuine outage — the
exact bug, undetected because the reproduction was too kind. So the implementation must cover both
families, and the tests must prove the guard against a **synthetic `ConnectionError` injected at
the constructor** (deterministic, no network needed) in addition to any offline-mode run.

Recommended tuple, to be confirmed by reproduction in task 2:

```python
_NETWORK_ERRORS: tuple[type[BaseException], ...] = (OSError,)
try:  # huggingface_hub's own errors, when the library is present
    from huggingface_hub.errors import (
        HfHubHTTPError,
        LocalEntryNotFoundError,
        OfflineModeIsEnabled,
    )
    _NETWORK_ERRORS += (HfHubHTTPError, LocalEntryNotFoundError, OfflineModeIsEnabled)
except ImportError:
    pass
```

`OSError` is the anchor: builtin `ConnectionError`, `requests.exceptions.ConnectionError`, and
`huggingface_hub`'s `LocalEntryNotFoundError` all derive from it, while an `AssertionError` from a
genuine embedding regression does not. This is a named, bounded set — not `except Exception` — and
it keeps the assertions capable of failing, which is the point of criterion 4.

### D5: A skip message that names the network, and docs that name the command

`pytest.skip` reasons are only visible with `-rs` or `-v`; the repo runs `-v` by default
(`pyproject.toml:75`). The reason string must contain the word "network" and the model name, so a
reader of a green run can tell the embedding path went untested and why. A test asserting the skip
message mentions the network keeps this from rotting into "skipped: unavailable".

Pair it with a developer-guide subsection giving the literal command, next to the existing smoke
instructions. Moving tests out of the gate is only defensible if finding them is trivial.

## Risks / Trade-offs

- **These benchmarks now run in no automated job.** Mitigated by D5 (documented command) and by
  the guard, which means a future job can adopt them without the flake returning. Accepted: the
  status quo is worse — they run on every PR and can red it for reasons unrelated to the diff.
- **`OSError` is broader than `ConnectionError`.** A corrupt local cache file would skip rather
  than fail. Trade-off accepted: the alternative is enumerating transport types across
  `requests`/`httpx`/`hf_xet` and missing one during the next outage, which is the failure this
  change exists to stop. The narrowing that matters — not catching `AssertionError` — holds.
- **A skip is a weaker signal than a pass.** If the CDN were down for a month, the benchmarks would
  skip for a month. The skip reason names the network so the cause is legible, but nothing alerts
  on it. Out of scope to build alerting for a manually-run benchmark.
- **Import cleanup in the donor file.** Removing two methods may orphan imports (`time`,
  `pytest`). `pytest` is still used elsewhere in the file; `time` may not be. Lint (`flake8` in
  the gate's CI order) catches an unused import, and task 3.4 checks explicitly rather than
  relying on it.

## Migration Plan

No migration. Tests move within the repo; no data, schema, config, or deployed artifact changes.
No coordination with the live deployment is required, and the change is safe to land at any time
relative to other open PRs — it touches no `src/` file, so it cannot conflict with the
`app.py`-heavy PRs currently open (#192 and the docs branch).

## Open Questions

None blocking. One deliberately deferred to a human, recorded in the PR body rather than resolved
here: whether the embedding benchmarks should eventually gate PRs again behind a pre-warmed CI
cache (issue option (c)). That decision needs `.github/workflows/**` access this change does not
have, and the guard added here is a prerequisite for it either way.
