# Issue #492 acceptance probe — results

Run on 2026-09-18 against the finished branch.

## Hole-A probe (verbatim — `t._FORCED_DECOMPRESSOR.findall(c)`)

The six previously-missed forms are now `FLAGGED`; `tar -xf f.tar.xz` remains `MISSED`
(correct — it uses auto-detection and must not trip the guard).

```
FLAGGED  'tar -x --gzip -f /tmp/f'
FLAGGED  'tar -x -z -f /tmp/f'
FLAGGED  'tar --lzip -xf /tmp/f'
FLAGGED  'tar --uncompress -xf /tmp/f'
FLAGGED  'tar -I zstd -xf /tmp/f'
FLAGGED  'tar --use-compress-program=zstd -xf /tmp/f'
MISSED   'tar -xf f.tar.xz'
```

## Hole-B probe (`_offenders` replaces the `_MOVING_DOWNLOAD`-filtered comprehension)

Input: download and extraction in separate `RUN` instructions.

```
split-RUN offenders: ['-xjf']   ← non-empty; hole B is closed
```

The original comprehension (`[found for c in _commands(text) if _MOVING_DOWNLOAD.search(c)
for found in _FORCED_DECOMPRESSOR.findall(c)]`) returns `[]` here because the second `RUN`
contains no moving-download URL — `_MOVING_DOWNLOAD.search` filters it out. Only
`_offenders` crosses the `RUN` boundary via the saved path.

## Template probe (`_offenders` across all 15 templates)

Six templates fetch a moving download; all report zero offenders.

```
Dockerfile-benchmarks-gpu:     0 offenders
Dockerfile-chat-gpu:           0 offenders
Dockerfile-data-manager-gpu:   0 offenders
Dockerfile-grader:             0 offenders
Dockerfile-grader-gpu:         0 offenders
Dockerfile-mattermost-gpu:     0 offenders
```

No template under `src/cli/templates/dockerfiles/` was edited by this change (`git diff
--stat` shows no changes there).
