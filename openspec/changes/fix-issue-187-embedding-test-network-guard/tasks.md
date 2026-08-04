# Tasks — fix issue #187: embedding tests must not let a CDN outage red a pull request

Run everything from the repo root. Environment for every task that runs tests or the gate:

```bash
export PATH=/home/austin/miniforge3/envs/archi/bin:$PATH
```

Run the gate **bare** — `bash scripts/gate.sh`. Piping or redirecting it trips the harness
protected-path guard and looks like a failure when it is not.

**Do not edit** `.github/workflows/**`, `scripts/gate.sh`, or any control-plane file. The whole
design depends on not needing to: the gate names `tests/unit/` explicitly, so relocation alone
takes these tests out of every gating path. If you find yourself wanting to edit the gate or CI,
stop and record why in `docs/questions.md` instead.

## 1. Pre-flight — confirm the premise still holds

- [x] 1.1 Confirm both call sites still look as the design describes:
      `grep -n "HuggingFaceEmbeddings\|except ImportError\|pytest.skip" tests/unit/test_ingestion_pipeline_isolation.py`
      expects the model construction at `:129-133` and `:227-232` and the two
      `except ImportError` → `pytest.skip` pairs at `:153-154` and `:277-278`. If the lines have
      moved, re-locate them and use the real numbers for the rest of these tasks.
- [x] 1.2 Confirm the gating suite is defined by an explicit path, which is what makes relocation
      sufficient: `scripts/gate.sh:146` runs `python -m pytest tests/unit/ --cov=src …`, and
      `pyproject.toml:74` sets `testpaths = ["tests/unit"]`. If the gate has changed to run
      `tests/` or to rely on `testpaths` alone, STOP — the design's central assumption is void and
      the change needs a human. Record it in `docs/questions.md`.
- [x] 1.3 Confirm the two tests cover no `src/` lines, so moving them costs the gating suite no
      coverage. Read both method bodies (`:116-154`, `:217-278`) and confirm they import only
      `langchain_*` and stdlib — no `src.` import. Cross-check by running just these two with
      coverage and confirming they add nothing:
      `python -m pytest "tests/unit/test_ingestion_pipeline_isolation.py::TestIngestionPipelineIsolation::test_embedding_model_works" "tests/unit/test_ingestion_pipeline_isolation.py::TestIngestionPipelineIsolation::test_embedding_performance_realistic" --cov=src --cov-report=term | tail -5`.
      If they do cover `src/` lines, record which in the PR body — the move then has a real
      coverage cost a reviewer must weigh.
- [x] 1.4 Confirm `tests/smoke/` is a viable home: `python -m pytest tests/smoke/ --collect-only -q | tail -3`
      must collect without error. Note that `tests/smoke/conftest.py:8` imports `psycopg2` at
      module scope, so collection there needs `psycopg2` importable; its fixtures are opt-in and
      will not run for the new tests. If collection errors, STOP and record it — the new file's
      home is not usable.
- [x] 1.5 Establish the baseline: with a warm cache and the network up,
      `python -m pytest tests/unit/test_ingestion_pipeline_isolation.py -v` passes. Record the
      wall-clock time of the two embedding tests — it is the number the PR body cites as the cost
      removed from the gating job.

## 2. Red test — prove the guard is missing before writing it

- [x] 2.1 Create `tests/smoke/test_embedding_benchmarks.py` and move both tests into it verbatim:
      `test_embedding_model_works` (from `:116-154`) and `test_embedding_performance_realistic`
      (from `:217-278`). Keep them in a class (e.g. `TestEmbeddingBenchmarks`), keep every
      assertion and timing printout unchanged, and at this step keep the guard exactly as it is —
      `except ImportError` only. Give the module a docstring saying these reach the HuggingFace CDN,
      are excluded from the gating suite deliberately, and cite issue #187. Do **not** delete
      anything from `tests/unit/` yet — a green new file before the old one is removed proves the
      move is faithful.
- [x] 2.2 Confirm the moved tests pass in their new home with the network up:
      `python -m pytest tests/smoke/test_embedding_benchmarks.py -v`. Both must execute and
      assert — not skip. If either skips here, the move dropped something; fix it before going on.
- [x] 2.3 Add the guard test that reproduces the defect **deterministically, without needing an
      outage**: a test that monkeypatches the embedding constructor to raise
      `ConnectionError("Network error: Request middleware error: error sending request for url "
      "(https://cas-server.xethub.hf.co/v2/reconstructions/abc)")` — the real 2026-08-02 exception
      — then invokes the shared load-or-skip helper and asserts the outcome is a **skip whose
      reason names the network**. Use `pytest.raises(pytest.skip.Exception)` (or
      `outcomes.Skipped`) and assert `"network"` appears in the reason, case-insensitively.
- [x] 2.4 Run it and **watch it fail**:
      `python -m pytest tests/smoke/test_embedding_benchmarks.py -k network -v`. It must fail
      because the `ConnectionError` escapes the `except ImportError` guard — that escape *is* the
      bug. A failure for any other reason (import error, helper not found) means the test is not
      reaching the guard; fix the test, not the source, until it fails for the right reason.
- [x] 2.5 Add the companion negative test that keeps the guard honest: monkeypatch the constructor
      to succeed but return a model whose `embed_documents` yields a wrong-dimension vector, and
      assert the test **fails** rather than skipping. This is the test that would catch a future
      widening of the guard to `except Exception`. Watch it behave correctly once 3.x lands.

## 3. The fix — broaden the guard, once, in a shared helper

- [x] 3.1 In `tests/smoke/test_embedding_benchmarks.py`, add the module-level exception tuple and
      the shared helper that both benchmarks call to obtain a model. Build the tuple as design D4
      specifies — anchored on `OSError`, extended with `huggingface_hub.errors`
      (`HfHubHTTPError`, `LocalEntryNotFoundError`, `OfflineModeIsEnabled`) inside a
      `try/except ImportError` so a missing `huggingface_hub` cannot break collection. **Never
      `except Exception`** — acceptance criterion 4 fails on a bare catch, and a bare catch would
      absorb the `AssertionError` from 2.5.
- [x] 3.2 Make the helper's skip reason name the network *and* the model, e.g.
      `f"embedding weights unreachable over the network ({model_name}): {exc!r}"`. Keep the
      missing-library skip reason distinct (`"langchain_huggingface not installed"`) so the two
      causes stay distinguishable in a `-v` run, per the spec.
- [x] 3.3 Route both relocated benchmarks through the helper, replacing their duplicated
      `try: … except ImportError:` blocks. The assertions and printouts stay exactly as they were;
      only model acquisition changes.
- [x] 3.4 Run the new file and confirm 2.3 now passes, 2.5 still fails-as-designed (i.e. the
      wrong-dimension case is reported as a failure, not a skip), and both benchmarks still
      execute and assert with the network up:
      `python -m pytest tests/smoke/test_embedding_benchmarks.py -v`.
- [x] 3.5 Verify the guard against the **other** exception family — the offline/local-entry errors,
      which are not `ConnectionError` (design D4's trap). Force a cold cache **without touching the
      developer's real cache**:
      `HF_HOME=$(mktemp -d) HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m pytest tests/smoke/test_embedding_benchmarks.py -v -rs`.
      Both benchmarks must be reported as **skipped with the network reason**, not failed. Do
      **not** delete `~/.cache/huggingface` — a temp `HF_HOME` gives the same cold-cache miss and
      costs no re-download.

## 4. Remove the originals from the gating suite

- [x] 4.1 Delete `test_embedding_model_works` (`:116-154`) and
      `test_embedding_performance_realistic` (`:217-278`) from
      `tests/unit/test_ingestion_pipeline_isolation.py`. Delete only those two methods; leave
      `test_text_splitter_produces_chunks` (`:156`) and the surrounding class intact.
- [x] 4.2 Clean up what the deletion orphaned: the two `import time` statements were method-local
      (`:126`, `:224`) and go with the methods. Confirm `pytest` is still referenced elsewhere in
      the file before assuming its import is still needed —
      `grep -n "pytest\." tests/unit/test_ingestion_pipeline_isolation.py`. Remove any import the
      deletion left unused; leave every still-used import alone.
- [x] 4.3 Confirm the gating suite no longer collects them and no longer touches the CDN:
      `python -m pytest tests/unit/ --collect-only -q | grep -c embedding` should find no
      embedding-benchmark entries, and
      `python -m pytest tests/unit/test_ingestion_pipeline_isolation.py -v` must pass with the two
      tests absent.
- [x] 4.4 Prove the headline claim — the gating suite is now indifferent to the CDN. Run the
      gating path with the network forced offline and a cold cache:
      `HF_HOME=$(mktemp -d) HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m pytest tests/unit/ -q | tail -5`.
      It must be green, and visibly faster than the 1091s (18m11s) the outage produced. This is
      acceptance criterion 1.
- [x] 4.5 Inspect `git diff` on the donor file and confirm it contains only the two method
      deletions and any orphaned-import removal — no unrelated black reflow hunks. If black
      reflowed neighbouring code, note it in the PR body so a reviewer is not surprised.

## 5. Documentation

- [x] 5.1 In `docs/docs/developer_guide.md`, next to the existing smoke-test instructions
      (`:230`, `./tests/smoke/combined_smoke.sh <deployment-name>`), add a short subsection for the
      embedding benchmarks: the literal command
      (`python -m pytest tests/smoke/test_embedding_benchmarks.py -v`), the fact that they download
      ~90 MB of weights from the HuggingFace CDN, that they take 30-50s per file on CPU, and that
      they are deliberately outside the gating suite so a CDN outage cannot red a pull request.
      This is the spec's discoverability requirement — without it the move is a deletion in
      practice.
- [x] 5.2 Confirm no surviving document claims these benchmarks run as part of the unit suite:
      `grep -rn "test_embedding\|embedding_performance" docs/ AGENTS.md README.md 2>/dev/null`.
      Update anything stale that turns up; if nothing does, record that in the PR body.

## 6. Gate and hand off

- [x] 6.1 Run the full gate bare: `bash scripts/gate.sh`. It must pass. Note that the gate measures
      `--cov=src` only, so these test-file changes carry no diff-coverage obligation; if
      diff-cover reports no measurable lines, that is expected, not a failure. Never `--no-verify`.
- [x] 6.2 Confirm the gate's own wall-clock did not regress and ideally improved, and record the
      before (task 1.5) and after numbers for the PR body.
- [x] 6.3 Commit with a short lowercase subject and no `Co-Authored-By` or AI-attribution
      trailers. Push the branch and open a PR into `fasrc/archi:dev` with `closes #187`.
      The PR body MUST state:
      (i) that **both (a) and (b)** from the issue's menu were taken, and why (a) alone was
      insufficient — the `ConnectionError` only arrives after the HF client exhausts its internal
      retries, so a guard alone converts a red 18-minute job into a green 18-minute job without
      removing the cost;
      (ii) that **(c)** — pre-caching weights in CI — was not chosen because it edits
      `.github/workflows/**`, which the unattended nightly may not touch, and that it remains open
      to a human as the way to bring these back into the gate;
      (iii) each acceptance criterion with its evidence (tasks 4.4, 3.5, 2.2/3.4, 3.1, 6.1);
      (iv) the before/after gating-suite timings.
      **Never merge** — a human merges, in daylight.
