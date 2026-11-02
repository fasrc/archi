## 1. Arm mode: a retry after a failed pin write finishes the pin

Each checkbox below is ONE commit, and the suite is green at each commit. Every Appendix block was run red and green in scratch copies of `scripts/` and `tests/unit/` on 2026-11-02 (`design.md` D5). Copy the blocks exactly; do not rewrite them. Each OLD block occurs exactly once in its file.

- [ ] 1.1 Test first, then implement, in ONE commit (a red-only commit cannot pass the gate). In `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`, insert the lines of Appendix A.1 directly after the header line `#   97. archive_run.sh refuses a run whose judge identity differs from the campaign's rows (G12)`, and insert the block of Appendix A.2 directly ABOVE the final `printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"` line. Run `bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`. The last line must be `109 passed, 3 failed`, and the three `not ok` lines must be `pin repair on retry`, `complete-run refusal` and `re-pin repair` (cases 113, 114, 120). Then make the four replacements of Appendix A.3 to A.6 in `scripts/benchmarking/feature_matrix/archive_run.sh`. Run `bash -n scripts/benchmarking/feature_matrix/archive_run.sh`, then the self-test again: the last line must be `112 passed, 0 failed`. Run `bash scripts/gate.sh` and commit (`fix(#677): finish a failed arm-mode pin write on retry`).

## 2. Sweep mode: a retry of run 1 finishes the absent pins

- [ ] 2.1 Test first, then implement, in ONE commit. In `tests/unit/test_sweep_tools.py`, insert the block of Appendix B.1 directly ABOVE the line `def test_a_v2_reading_is_usable():`. Its three test names are new in the file: `grep -c 'def test_a_retry_after_a_failed_pin_write_finishes_both_pins\|def test_a_second_archive_of_a_complete_run_one_is_still_refused\|def test_a_retry_that_is_not_a_failed_pin_write_is_refused' tests/unit/test_sweep_tools.py` must print `3`. Run `python -m pytest -q tests/unit/test_sweep_tools.py`: expect `2 failed, 44 passed` (the two `test_a_retry_after_a_failed_pin_write_finishes_both_pins` parameters fail with `is already archived`). Then make the three replacements of Appendix B.2 to B.4 in `scripts/benchmarking/feature_matrix/sweep_tools.py`. Run the test file again: `46 passed`. Run `bash scripts/gate.sh` and commit (`fix(#677): finish failed sweep pin writes on retry`).

## 3. Docs

- [ ] 3.1 Make the replacements of Appendix C.1 and C.2 in `docs/docs/proposals/feature-matrix-campaign-2026.md` and of Appendix C.3 in `scripts/benchmarking/README.md`. Run `bash scripts/gate.sh` and commit (`docs(#677): describe the pin repair on retry`).

## 4. Verify, push, and open the PR

- [ ] 4.1 Run `bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` (last line `112 passed, 0 failed`), `python -m pytest -q tests/unit/test_sweep_tools.py` (`46 passed`) and `openspec validate fix-issue-677-pin-repair-on-retry --strict`. Then tick this box, commit the tick through the hook (`bash scripts/gate.sh` runs in it), push with `git push -u origin fix/issue-677-pin-repair-on-retry`, and open the PR with `gh pr create --repo fasrc/archi --base dev --body-file <file>`. Write the body file OUTSIDE the repository (for example `/tmp/pr-677-body.md`); never add it to the repository. Put `closes #677` in the PR **body**, not the title. In the body, state how `design.md` D2 reads the operator's decision: a re-pin retry needs `--new-corpus`, and a pin that holds any other value is kept. No `Co-Authored-By` trailer. Do not merge.

## Appendix: exact blocks (no checkboxes)

### A.1 Self-test index lines

Insert after the `#   97.` header line of `test_feature_matrix_wrappers.sh`:

```bash
#   113. a retry of check 80's archive (ledger row durable, pin write failed) writes the pin,
#        logs "repaired the corpus pin", exits 0, and appends no second row (#677)
#   114. a second archive of that complete run is still refused; ledger and pin unchanged
#   115. a row with no artifact_sha256 (written before #677) is not repaired: refused, no pin
#   116. a matching row that is not the stack's newest ragas row is not repaired: refused, no pin
#   117. an artifact whose bytes changed since its row was written is not repaired: refused, no pin
#   118. a pin that holds another value is not overwritten for a first-pin row: refused, pin kept
#   119. a --new-corpus re-pin whose pin write failed, retried without --new-corpus: refused,
#        old pin kept
#   120. the same retry with --new-corpus finishes the re-pin and exits 0
```

### A.2 Self-test cases 113-120

Insert directly above the final `printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"` line. Case 113 retries check 80's archive, so the block must come after check 80; the cases after check 82 do not touch `fm-pin80`.

```bash
# --- #677: a retry after a failed pin write finishes the pin -----------------------------
A80="$FM_OUT/benchmarking-fm-pin80-20260903_000023.json"
pin80_rows() { "$FM_PYTHON" -c "import json,sys; print(sum(1 for e in json.load(open(sys.argv[1])) if e.get('kind')=='ragas' and e.get('stack')=='fm-pin80'))" "$FM_OUT/ledger.json"; }

# 113: check 80 left a durable ledger row and no pin. The same archive, retried with the
# real python, finishes the pin write and exits 0; it appends no second row for the run.
ROWS_BEFORE="$(ledger_rows)"
run bash "$HERE/archive_run.sh" 00 1 "$T/arms/00-baseline.yaml" --stack fm-pin80
if [ "$RC" = 0 ] && grep -q 'repaired the corpus pin for fm-pin80' "$T/stdout" && ! grep -q 'archived arm' "$T/stdout" \
   && [ "$(cat "$FM_OUT/corpus-pin-fm-pin80" 2>/dev/null)" = sha256:pin80 ] && [ "$(ledger_rows)" = "$ROWS_BEFORE" ] \
   && [ "$(pin80_rows)" = 1 ] && ! compgen -G "$FM_OUT/corpus-pin-fm-pin80.tmp.*" >/dev/null; then
  ok "a retry after a failed pin write finishes the pin and exits 0"; else notok "pin repair on retry (rc=$RC: $(cat "$T/stdout" "$T/stderr"))"; fi

# 114: the run is complete now, so a second archive of it is still refused as a duplicate
ROWS_BEFORE="$(ledger_rows)"
run bash "$HERE/archive_run.sh" 00 1 "$T/arms/00-baseline.yaml" --stack fm-pin80
if [ "$RC" != 0 ] && grep -q 'already archived' "$T/stderr" && ! grep -q 'repaired' "$T/stdout" \
   && [ "$(ledger_rows)" = "$ROWS_BEFORE" ] && [ "$(cat "$FM_OUT/corpus-pin-fm-pin80" 2>/dev/null)" = sha256:pin80 ]; then
  ok "a second archive of a complete run is still refused"; else notok "complete-run refusal (rc=$RC: $(cat "$T/stdout" "$T/stderr"))"; fi

# 115-117 remove the pin and change one thing the repair checks; each retry must be refused
# and must write no pin. The ledger and the artifact are restored after each check.
cp "$FM_OUT/ledger.json" "$T/ledger.677"; cp -p "$A80" "$T/artifact.677"
rm -f "$FM_OUT/corpus-pin-fm-pin80"

# 115: a row written before #677 has no artifact_sha256, so it cannot prove the file is the same
"$FM_PYTHON" -c "import json,sys; p=sys.argv[1]; rows=json.load(open(p)); [e.pop('artifact_sha256', None) for e in rows if e.get('stack')=='fm-pin80']; json.dump(rows, open(p, 'w'), indent=1)" "$FM_OUT/ledger.json"
run bash "$HERE/archive_run.sh" 00 1 "$T/arms/00-baseline.yaml" --stack fm-pin80
cp "$T/ledger.677" "$FM_OUT/ledger.json"
if [ "$RC" != 0 ] && grep -q 'already archived' "$T/stderr" && [ ! -e "$FM_OUT/corpus-pin-fm-pin80" ]; then
  ok "a row with no artifact_sha256 is not repaired"; else notok "legacy-row repair guard (rc=$RC: $(cat "$T/stdout" "$T/stderr"))"; fi

# 116: a later ragas row on the stack means the pin may have moved since; not repaired
"$FM_PYTHON" -c "import json,sys; p=sys.argv[1]; rows=json.load(open(p)); rows.append({'kind': 'ragas', 'arm': '00', 'stack': 'fm-pin80', 'run': 2, 'artifact': 'later.json', 'corpus_fingerprint': 'sha256:pin80'}); json.dump(rows, open(p, 'w'), indent=1)" "$FM_OUT/ledger.json"
run bash "$HERE/archive_run.sh" 00 1 "$T/arms/00-baseline.yaml" --stack fm-pin80
cp "$T/ledger.677" "$FM_OUT/ledger.json"
if [ "$RC" != 0 ] && grep -q 'already archived' "$T/stderr" && [ ! -e "$FM_OUT/corpus-pin-fm-pin80" ]; then
  ok "a row that is not the stack's newest ragas row is not repaired"; else notok "newest-row repair guard (rc=$RC: $(cat "$T/stdout" "$T/stderr"))"; fi

# 117: the artifact's bytes changed since its row was written (same path, another sha256)
printf '\n' >> "$A80"
run bash "$HERE/archive_run.sh" 00 1 "$T/arms/00-baseline.yaml" --stack fm-pin80
cp -p "$T/artifact.677" "$A80"
if [ "$RC" != 0 ] && grep -q 'already archived' "$T/stderr" && [ ! -e "$FM_OUT/corpus-pin-fm-pin80" ]; then
  ok "an artifact whose bytes changed is not repaired"; else notok "artifact-digest repair guard (rc=$RC: $(cat "$T/stdout" "$T/stderr"))"; fi

# 118: a pin that holds another value was not left by a failed first-pin write; it is kept
printf 'sha256:other\n' > "$FM_OUT/corpus-pin-fm-pin80"
run bash "$HERE/archive_run.sh" 00 1 "$T/arms/00-baseline.yaml" --stack fm-pin80
if [ "$RC" != 0 ] && grep -q 'already archived' "$T/stderr" && [ "$(cat "$FM_OUT/corpus-pin-fm-pin80")" = sha256:other ]; then
  ok "a pin that holds another value is not overwritten"; else notok "other-pin repair guard (rc=$RC: $(cat "$T/stdout" "$T/stderr"))"; fi
rm -f "$FM_OUT/corpus-pin-fm-pin80"

# 119-120: a closing-baseline re-pin (--new-corpus) whose pin write fails after the ledger
# append leaves the old pin in place. A fresh stack: run 1 pins p119a; then a fresh deploy
# and a run-2 re-pin to p119b that fails at the pin write (check 80's shim).
mkdir -p "$ARCHI_DIR/archi-fm-pin119"
run env RAGAS_ENV_FILE="$T/judge.env" bash "$HERE/run_arm.sh" 00 "$T/arms/00-baseline.yaml" --stack fm-pin119
artifact "$FM_OUT/benchmarking-fm-pin119-20260903_000040.json" '[]' p119a 5
run bash "$HERE/archive_run.sh" 00 1 "$T/arms/00-baseline.yaml" --stack fm-pin119
R1=$RC
run env RAGAS_ENV_FILE="$T/judge.env" bash "$HERE/run_arm.sh" 00 "$T/arms/00-baseline.yaml" --stack fm-pin119
rm -f "$FM_OUT"/benchmarking-fm-pin119-*.json
artifact "$FM_OUT/benchmarking-fm-pin119-20260903_000041.json" '[]' p119b 5
FM_PYTHON="$T/bin/python-pin-fail"
run bash "$HERE/archive_run.sh" 00 2 "$T/arms/00-baseline.yaml" --stack fm-pin119 --new-corpus
FM_PYTHON="$REAL_PY"
R2=$RC
if [ -f "$FM_OUT" ]; then rm -f "$FM_OUT"; mv "$FM_OUT.moved" "$FM_OUT"; fi

# 119: the retry without --new-corpus is refused: moving an existing pin needs the flag
run bash "$HERE/archive_run.sh" 00 2 "$T/arms/00-baseline.yaml" --stack fm-pin119
if [ "$R1" = 0 ] && [ "$R2" != 0 ] && [ "$RC" != 0 ] && grep -q 'already archived' "$T/stderr" \
   && [ "$(cat "$FM_OUT/corpus-pin-fm-pin119" 2>/dev/null)" = sha256:p119a ]; then
  ok "a re-pin retry without --new-corpus is refused and keeps the old pin"; else notok "re-pin repair needs --new-corpus (r1=$R1 r2=$R2 rc=$RC: $(cat "$T/stdout" "$T/stderr"))"; fi

# 120: the same retry with --new-corpus finishes the re-pin and exits 0
ROWS_BEFORE="$(ledger_rows)"
run bash "$HERE/archive_run.sh" 00 2 "$T/arms/00-baseline.yaml" --stack fm-pin119 --new-corpus
if [ "$RC" = 0 ] && grep -q 'repaired the corpus pin for fm-pin119' "$T/stdout" \
   && [ "$(cat "$FM_OUT/corpus-pin-fm-pin119" 2>/dev/null)" = sha256:p119b ] && [ "$(ledger_rows)" = "$ROWS_BEFORE" ]; then
  ok "a re-pin retry with --new-corpus finishes the re-pin"; else notok "re-pin repair (rc=$RC: $(cat "$T/stdout" "$T/stderr"))"; fi
```

### A.3 `archive_run.sh`: header comment

OLD:

```bash
# collection and embedding model from the artifact's retrieval_identity (null if unrecorded).
set -euo pipefail
```

NEW:

```bash
# collection and embedding model from the artifact's retrieval_identity (null if unrecorded),
# and artifact_sha256, the sha256 of the archived file.
# A retry after a failed pin write is not refused (#677): when the stack's newest ragas row
# records this artifact (same path and artifact_sha256) as this arm and run, and the pin is
# in the state a failed write leaves (absent after a first pin; the row's repinned_from
# after a re-pin, retried with --new-corpus), it writes the row's fingerprint to the pin,
# logs "repaired the corpus pin", appends no row, and exits 0. Every other duplicate is
# still refused.
set -euo pipefail
```

### A.4 `archive_run.sh`: the first validator checks for a repairable retry

OLD:

```bash
FM_ERRF="$(mktemp)"
if ! FM_LEDGER="$(fm_ledger)" FM_ARTIFACT="$ARTIFACT" FM_DATED_BY="$DATED_BY" FM_STACK="$STACK" FM_ARM="$ARM" FM_RUN="$RUN" FM_PIN_FILE="$(fm_pin_file "$STACK")" FM_ACTIVE_LOCK_SHA="$(fm_lock_sha)" "$FM_PYTHON" - 2>"$FM_ERRF" <<'EOF'
import datetime as dt, json, os, sys
ledger, artifact, stack = os.environ["FM_LEDGER"], os.environ["FM_ARTIFACT"], os.environ["FM_STACK"]
arm, run = os.environ["FM_ARM"], int(os.environ["FM_RUN"])
rows = json.load(open(ledger)) if os.path.exists(ledger) else []
dup = [r for r in rows if r.get("artifact") == artifact]
```

NEW:

```bash
FM_ERRF="$(mktemp)"; FM_RC=0
FM_LEDGER="$(fm_ledger)" FM_ARTIFACT="$ARTIFACT" FM_DATED_BY="$DATED_BY" FM_STACK="$STACK" FM_ARM="$ARM" FM_RUN="$RUN" FM_PIN_FILE="$(fm_pin_file "$STACK")" FM_ACTIVE_LOCK_SHA="$(fm_lock_sha)" FM_NEW_CORPUS="$NEW_CORPUS" "$FM_PYTHON" - 2>"$FM_ERRF" <<'EOF' || FM_RC=$?
import datetime as dt, hashlib, json, os, sys
ledger, artifact, stack = os.environ["FM_LEDGER"], os.environ["FM_ARTIFACT"], os.environ["FM_STACK"]
arm, run = os.environ["FM_ARM"], int(os.environ["FM_RUN"])
rows = json.load(open(ledger)) if os.path.exists(ledger) else []
# #677: a retry after a failed pin write. The stack's newest ragas row records this exact
# artifact (path and sha256) as this arm and run, and the pin is in the state a failed
# write leaves: absent after a first pin, or still the old pin (repinned_from) after a
# re-pin, which as always needs --new-corpus. Finish the pin write and stop (exit 3 =
# repaired, no new row). Every other duplicate falls through to the refusals below.
pin_file = os.environ["FM_PIN_FILE"]
newest = ([r for r in rows if r.get("kind") == "ragas" and r.get("stack") == stack] or [{}])[-1]
if (newest.get("artifact") == artifact and newest.get("arm") == arm and newest.get("run") == run
        and newest.get("artifact_sha256") == hashlib.sha256(open(artifact, "rb").read()).hexdigest()
        and newest.get("corpus_fingerprint") and not os.path.isdir(pin_file)):
    pin = open(pin_file).read().strip() if os.path.exists(pin_file) else None
    old = newest.get("repinned_from")
    if (pin is None) if old is None else (pin == old and os.environ["FM_NEW_CORPUS"] == "true"):
        want = newest["corpus_fingerprint"]
        tmp = f"{pin_file}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w") as f:
                f.write(want + "\n")
            os.replace(tmp, pin_file)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        print(f"repaired the corpus pin for {stack}: {want} (arm {arm} run {run} was already in the ledger; its pin write had failed)", file=sys.stderr)
        sys.exit(3)
dup = [r for r in rows if r.get("artifact") == artifact]
```

### A.5 `archive_run.sh`: the shell handles exit 3 (repaired)

This is the `EOF`/`then` block that closes the first validator, directly above `# The live document and chunk counts`.

OLD:

```bash
EOF
then
  cat "$FM_ERRF" >&2
  FM_REASON="$(cat "$FM_ERRF")"; rm -f "$FM_ERRF"
  fm_die_paged "stack $STACK arm $ARM" "refusing to archive $ARTIFACT (see above)" "$FM_REASON"
fi
rm -f "$FM_ERRF"
```

NEW:

```bash
EOF
if [ "$FM_RC" = 3 ]; then
  fm_log "$(cat "$FM_ERRF")"; rm -f "$FM_ERRF"
  exit 0
fi
if [ "$FM_RC" != 0 ]; then
  cat "$FM_ERRF" >&2
  FM_REASON="$(cat "$FM_ERRF")"; rm -f "$FM_ERRF"
  fm_die_paged "stack $STACK arm $ARM" "refusing to archive $ARTIFACT (see above)" "$FM_REASON"
fi
rm -f "$FM_ERRF"
```

### A.6 `archive_run.sh`: the second validator records `artifact_sha256`

Two one-line replacements inside the second validator heredoc.

OLD:

```python
import importlib.util, json, math, os, sys, yaml
```

NEW:

```python
import hashlib, importlib.util, json, math, os, sys, yaml
```

OLD:

```python
    "finished": os.environ["FM_FINISHED"], "artifact": p,
```

NEW:

```python
    "finished": os.environ["FM_FINISHED"], "artifact": p, "artifact_sha256": hashlib.sha256(open(p, "rb").read()).hexdigest(),
```

### B.1 `tests/unit/test_sweep_tools.py`: new tests

Insert directly above `def test_a_v2_reading_is_usable():`, followed by two blank lines.

```python
@pytest.mark.parametrize("failed_pin", ["corpus-pin-r0", "category-map-pin-r0"])
def test_a_retry_after_a_failed_pin_write_finishes_both_pins(
    sweep, monkeypatch, capsys, failed_pin
):
    """#677: the ledger rows are durable, a pin replace failed; a retry finishes."""
    lock = _lock(sweep)
    artifact, digest = _artifact(sweep)
    ledger = _ledger(sweep)
    census = _census(sweep, digest, lock)
    real_replace = os.replace

    def failing_replace(src, dst):
        if os.path.basename(os.fspath(dst)) == failed_pin:
            raise OSError("pin write failed")
        return real_replace(src, dst)

    monkeypatch.setattr(st.os, "replace", failing_replace)
    with pytest.raises(OSError, match="pin write failed"):
        _archive(sweep, lock, artifact, 1, census)
    assert len(json.loads(ledger.read_text())) == 4
    assert not (sweep / "out" / failed_pin).exists()

    monkeypatch.setattr(st.os, "replace", real_replace)
    rows = _archive(sweep, lock, artifact, 1, census)

    assert [row["arm"] for row in rows] == STEMS
    assert len(json.loads(ledger.read_text())) == 4
    assert (sweep / "out/corpus-pin-r0").read_text().strip() == "sha256:corpus"
    assert (sweep / "out/category-map-pin-r0").read_text().strip() == digest
    assert f"repaired {failed_pin}" in capsys.readouterr().out


def test_a_second_archive_of_a_complete_run_one_is_still_refused(sweep, capsys):
    lock = _lock(sweep)
    artifact, digest = _artifact(sweep)
    ledger = _ledger(sweep)
    census = _census(sweep, digest, lock)
    _archive(sweep, lock, artifact, 1, census)
    before = ledger.read_text()

    with pytest.raises(st.SweepError, match="already archived"):
        _archive(sweep, lock, artifact, 1, census)
    assert ledger.read_text() == before
    assert (sweep / "out/corpus-pin-r0").read_text().strip() == "sha256:corpus"
    assert "repaired" not in capsys.readouterr().out


@pytest.mark.parametrize("tamper", ["later-row", "no-map-digest", "other-corpus-pin"])
def test_a_retry_that_is_not_a_failed_pin_write_is_refused(sweep, tamper):
    """A later run on the stack, rows with no map digest, or a pin that holds
    another value: none is the state a failed pin write leaves, so no repair."""
    lock = _lock(sweep)
    artifact, digest = _artifact(sweep)
    ledger = _ledger(sweep)
    census = _census(sweep, digest, lock)
    _archive(sweep, lock, artifact, 1, census)
    rows = json.loads(ledger.read_text())
    if tamper == "later-row":
        rows.append(dict(rows[-1], run=2, artifact="later.json", artifact_sha256="0"))
    elif tamper == "no-map-digest":
        for row in rows:
            row.pop("census_map_digest", None)
    ledger.write_text(json.dumps(rows))
    corpus_pin = sweep / "out/corpus-pin-r0"
    corpus_pin.unlink()
    (sweep / "out/category-map-pin-r0").unlink()
    if tamper == "other-corpus-pin":
        corpus_pin.write_text("sha256:other\n")

    with pytest.raises(st.SweepError, match="already archived"):
        _archive(sweep, lock, artifact, 1, census)
    assert not (sweep / "out/category-map-pin-r0").exists()
    kept = corpus_pin.read_text().strip() if corpus_pin.exists() else None
    assert kept == ("sha256:other" if tamper == "other-corpus-pin" else None)
```

### B.2 `sweep_tools.py`: import

OLD:

```python
from typing import Any, Dict, List, Optional
```

NEW:

```python
from typing import Any, Dict, List, Optional, Tuple
```

### B.3 `sweep_tools.py`: the helper

Insert directly above `def archive(`, with two blank lines before and after it.

```python
def _finish_pins(
    rows: List[dict],
    *,
    stack: str,
    run: int,
    archived: str,
    digest: str,
    pins_dir: Path,
) -> Tuple[List[dict], List[Path]]:
    """Finish the pin writes of a run whose ledger rows are durable (#677).

    ``archive`` replaces the ledger, then writes the pins; a failure between the
    two leaves rows that a retry would refuse as a duplicate. When the stack's
    newest ragas rows are this run's rows for this exact artifact (archived path
    and sha256), each absent pin is written with the value those rows record. A
    pin that exists must already hold that value: anything else was not left by
    a failed write, so nothing is repaired. Only run 1 rows carry
    ``census_map_digest``, so only run 1 can be repaired. Returns the run's rows
    and the pins written; no pin written means there is nothing to repair.
    """
    ragas = [r for r in rows if r.get("kind") == "ragas" and r.get("stack") == stack]
    mine = [
        r
        for r in ragas
        if r.get("run") == run
        and r.get("artifact") == archived
        and r.get("artifact_sha256") == digest
    ]
    if not mine or ragas[-1] is not mine[-1]:
        return mine, []
    want = (
        (pins_dir / f"corpus-pin-{stack}", mine[-1].get("corpus_fingerprint")),
        (pins_dir / f"category-map-pin-{stack}", mine[-1].get("census_map_digest")),
    )
    if not all(value for _, value in want):
        return mine, []
    for pin, value in want:
        if pin.exists() and not (pin.is_file() and pin.read_text().strip() == value):
            return mine, []
    written: List[Path] = []
    for pin, value in want:
        if pin.exists():
            continue
        pin_tmp = pin.with_name(pin.name + ".tmp")
        pin_tmp.write_text(f"{value}\n")
        os.replace(pin_tmp, pin)
        written.append(pin)
    return mine, written
```

### B.4 `sweep_tools.py`: the duplicate check in `archive()`

OLD:

```python
    digest = _sha(artifact)
    if any(row.get("artifact_sha256") == digest for row in rows):
        raise SweepError(f"{artifact} is already archived")
```

NEW:

```python
    digest = _sha(artifact)
    if any(row.get("artifact_sha256") == digest for row in rows):
        mine, written = _finish_pins(
            rows,
            stack=stack,
            run=run,
            archived=str(Path(dest) / artifact.name),
            digest=digest,
            pins_dir=Path(pins_dir),
        )
        if not written:
            raise SweepError(f"{artifact} is already archived")
        names = ", ".join(pin.name for pin in written)
        print(
            f"repaired {names} for run {run} on {stack}; "
            "its ledger rows were already written"
        )
        return mine
```

### C.1 `docs/docs/proposals/feature-matrix-campaign-2026.md`: the archive paragraph

OLD:

```markdown
that wrote nothing must not re-archive run 1 as run 2). Every wrapper also refuses an arm
label that does not match the YAML's own `name: fm-<arm>`, and a checkout whose HEAD is
not the locked campaign code or that carries uncommitted source changes. It records:
artifact path, `corpus_fingerprint`, `corpus_snapshot_id`, `config_version.digest`,
```

NEW:

```markdown
that wrote nothing must not re-archive run 1 as run 2). One duplicate is not refused: a
retry after a failed pin write (#677). When the stack's newest RAGAS row records the same
artifact (same path and `artifact_sha256`) as the same arm and run, and the pin is in the
state a failed write leaves (absent after a first pin; the row's `repinned_from` after a
`--new-corpus` re-pin, retried with `--new-corpus`), the wrapper writes the pin, logs
`repaired the corpus pin`, appends no row, and exits 0. Sweep mode does the same for run 1
and writes each absent pin. A pin that holds any other value is never overwritten. Every
wrapper also refuses an arm label that does not match the YAML's own `name: fm-<arm>`, and
a checkout whose HEAD is not the locked campaign code or that carries uncommitted source
changes. It records: artifact path and `artifact_sha256`, `corpus_fingerprint`,
`corpus_snapshot_id`, `config_version.digest`,
```

### C.2 `docs/docs/proposals/feature-matrix-campaign-2026.md`: the ledger example in section 9

OLD:

```json
 "artifact": "bench_out/feature_matrix/benchmarking-fm-03-<ts>.json",
```

NEW:

```json
 "artifact": "bench_out/feature_matrix/benchmarking-fm-03-<ts>.json", "artifact_sha256": "…",
```

### C.3 `scripts/benchmarking/README.md`: the `archive_run.sh` entry

OLD:

```markdown
  (`--new-corpus`: arm 00, after a fresh deploy, old pin recorded). Every wrapper refuses
```

NEW:

```markdown
  (`--new-corpus`: arm 00, after a fresh deploy, old pin recorded). A retry after a failed
  pin write is not refused (#677): when the stack's newest ragas row records the same
  artifact (path and `artifact_sha256`) as the same arm and run, and the pin is still absent
  (a first pin) or still the row's `repinned_from` (a re-pin, retried with `--new-corpus`),
  it writes the pin, logs `repaired the corpus pin`, and exits 0. `--sweep` does the same for
  the two run-1 pins. Every wrapper refuses
```
