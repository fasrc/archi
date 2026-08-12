## Why

The embedding network guard decides, for every exception raised while fetching model weights,
whether the condition is "the network could not answer" (a skip) or "the code or its model
dependency is broken" (a failure). That decision is the whole reason the embedding benchmarks can
live outside the gate without going quiet: get it wrong in the permissive direction and a renamed,
gated or removed model repository becomes a permanent green-by-skip; get it wrong in the restrictive
direction and a real CDN outage reds a deliberate run.

None of that logic is exercised by the suite that gates pull requests.

The classifier and its tests live entirely in `tests/smoke/test_embedding_benchmarks.py`
(`_NETWORK_ERROR_TYPES` `:66`, `_NETWORK_ERRNOS` `:123`, `_is_transient_status` `:147`,
`_GUARDED_ERRORS` `:168`, `_is_network_failure` `:171`, `_import_or_skip` `:238`), and
`pyproject.toml:74` sets `testpaths = ["tests/unit"]`. The gate collects nothing from
`tests/smoke/`, so:

```
python -m pytest tests/unit/ --collect-only -q | grep -ci embedding_guard   # 0
```

A pull request may today widen the allowlist to an over-broad base class, drop an entire exception
family, or break `_import_or_skip` into swallowing a genuinely broken environment, and **the gate
stays green**. That is not a hypothetical failure mode: the over-broad-base defect was introduced
and caught three review rounds in a row during #187 — once per HTTP library — and the test that
polices it (`test_no_named_network_type_drags_in_a_local_defect`) is itself ungated, so a fourth
regression would surface only as a false skip in a run nobody is watching.

The guard cannot simply be moved into the gate wholesale. Most of its tests monkeypatch
`langchain_huggingface.HuggingFaceEmbeddings.__init__` by string, which pytest resolves by importing
the library — a ~2s import tax on every gated run, and a dependency the gating suite deliberately
does not have. But the monkeypatching is not what needs gating; the *classification* is, and
classification needs no library at all. Only synthetic exceptions.

## What Changes

- **Extract the pure classification layer** out of the smoke test and into a new
  `tests/support/embedding_guard.py`, importable by both suites: `_NETWORK_ERROR_TYPES` and the
  conditional families that build it, `_HUB_HTTP_ERROR`, `_NETWORK_ERRNOS`,
  `_TRANSIENT_4XX_STATUSES`, `_is_transient_status`, `_GUARDED_ERRORS`, `_is_network_failure`,
  `_import_or_skip`, and the two test helpers the negative tests need, `_assert_propagates` and
  `_response`. There is **one** definition afterwards, not two.
- **Leave the network-touching and library-touching parts where they are.** `_load_model` (which
  imports `langchain_huggingface`), `TestEmbeddingBenchmarks`, `TestEmbeddingGuard` and
  `TestTheGuardTestsDegradeLikeTheCodeTheyGuard` all stay in `tests/smoke/`, now importing the
  shared module instead of defining the logic inline.
- **Add `tests/unit/test_embedding_guard_classifier.py`**, collected by the gate, driving the
  classifier directly with synthetic exceptions: every named transport type is recognised, every
  errno in the table is recognised via `OSError(errno)`, an `AssertionError` and a local `OSError`
  propagate rather than being absorbed, a definitive error status fails while a transient or
  server-side one skips, and `_import_or_skip` skips only for a genuinely absent module while a
  broken transitive import still raises.
- **Gate the allowlist invariant.** The subclass-inspection assertion that stops an over-broad base
  class from being readmitted is added to the unit suite, so widening the allowlist reds a pull
  request instead of quietly converting client-side defects into skips.
- **Verify by mutation, not by assertion.** Breaking the classifier on purpose
  (`_is_network_failure` → `return True`) must red `bash scripts/gate.sh`; the change is not done
  until that has been observed and reverted.

**Explicitly not changed:** `pyproject.toml`'s `testpaths`, the gate script, and any CI workflow —
the new tests land inside the path the gate already collects, so nothing in the control plane moves.
`TestEmbeddingGuard` is not promoted to the unit suite, and the guard is not promoted to `src/`.
No test added by this change imports `langchain_huggingface` or touches the network.

## Impact

- **Affected specs:** `gating-suite-hermeticity` — two new requirements. The capability's baseline
  is still pending in `fix-issue-187-embedding-test-network-guard`, so these are `## ADDED
  Requirements`, complementing rather than modifying #187's: #187 says the guard must not be *in*
  the gate, this says its classification must be *checked by* the gate. Both hold at once because
  the gated tests are hermetic.
- **Affected code:** `tests/support/embedding_guard.py` (new), `tests/support/__init__.py` (new),
  `tests/unit/test_embedding_guard_classifier.py` (new),
  `tests/smoke/test_embedding_benchmarks.py` (imports replace inline definitions).
- **Affected runtime:** none. No file under `src/` changes, so no shipped behaviour moves and the
  deployment is untouched.
- **Gate cost:** one new unit module of pure-Python assertions, no library import, no network.
