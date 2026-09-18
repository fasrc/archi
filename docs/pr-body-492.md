Closes #492

## Summary

- **Hole A** — the regex `_FORCED_DECOMPRESSOR` was replaced with a token-level scan (`_forced_decompressors`) that walks each `tar` invocation's token span and matches long forms whole and short forms case-sensitively inside clusters. A module-level adapter class keeps the name `_FORCED_DECOMPRESSOR` with a `.findall()` interface so all four existing call sites (including two inside frozen tests) are unchanged.
- **Hole B** — new `_saved_paths` reads `wget -O`/`curl -o`/`curl --output` destinations across all commands; new `_offenders` routes each forcing `tar` invocation through the three-branch logic in `design.md`: report if a known saved path appears in the invocation's own token span, report if the invocation shares a command with a moving download and the saved file is unresolvable, otherwise clean. The `test_a_moving_download_is_not_extracted_with_a_forced_decompressor` template test is routed through `_offenders`; no other call site changed.
- **Negative / boundary guards** — new test methods cover near-miss options (`--auto-compress`, `--no-auto-compress`, `-i`/`-I` case distinction, `--exclude=*.gz`), invocation-boundary isolation across `&&`/`;`/`|`, false-positive narrowing (different operand in same command), stdout-sink detection, glued-flag forms, bare-basename matching, and directory-prefix false-positive prevention.
- Three frozen tests (`test_auto_detection_is_left_alone`, `test_the_two_are_not_paired_across_separate_commands`, `TestAVersionedDownloadMayForceItsFormat`) are byte-identical to the baseline at `4b253e26`. No template under `src/cli/templates/dockerfiles/` was modified.
- **Docstring correction (task 3.1)** — the issue's "One more thing" section prescribes wording saying CI builds service images *post-merge only*, citing `publish-base-images.yml`. That claim is wrong: that workflow builds **base** images; the pre-merge `pr-preview.yml` job does build service images (the `chatbot` slice: `Dockerfile-chat`, `Dockerfile-postgres`, `Dockerfile-data-manager`). None of the six templates that fetch this download is in that slice. The docstring was updated to the accurate description rather than copying the issue's incorrect prescription.

## Acceptance probe output (task 4.1)

### Hole-A probe (verbatim — `t._FORCED_DECOMPRESSOR.findall(c)`)

```
FLAGGED  'tar -x --gzip -f /tmp/f'
FLAGGED  'tar -x -z -f /tmp/f'
FLAGGED  'tar --lzip -xf /tmp/f'
FLAGGED  'tar --uncompress -xf /tmp/f'
FLAGGED  'tar -I zstd -xf /tmp/f'
FLAGGED  'tar --use-compress-program=zstd -xf /tmp/f'
MISSED   'tar -xf f.tar.xz'
```

All six previously-missed forms are `FLAGGED`; `tar -xf f.tar.xz` remains `MISSED` (correct — it uses auto-detection and must not trip the guard).

### Hole-B probe (`_offenders` replaces the `_MOVING_DOWNLOAD`-filtered comprehension)

```
split-RUN offenders: ['-xjf']   ← non-empty; hole B is closed
```

The original comprehension filtered out the second `RUN` (no moving-download URL), returning `[]`. Only `_offenders` crosses the `RUN` boundary via the saved path.

### Template probe (`_offenders` across all 15 templates)

```
Dockerfile-benchmarks-gpu:     0 offenders
Dockerfile-chat-gpu:           0 offenders
Dockerfile-data-manager-gpu:   0 offenders
Dockerfile-grader:             0 offenders
Dockerfile-grader-gpu:         0 offenders
Dockerfile-mattermost-gpu:     0 offenders
```

Six templates fetch a moving download; all report zero offenders. No template was edited (`git diff --stat` shows no changes under `src/cli/templates/dockerfiles/`).
