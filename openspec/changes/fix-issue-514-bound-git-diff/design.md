# Design — bounding the captured git diff

## D1. Where the code goes

`src/cli/managers/git_diff_capture.py`, a sibling of `source_version.py` (95 lines), which is
the precedent for a small provenance seam that `templates_manager.py` imports. A separate
module keeps every new line in a file that is 100% exercised by its own tests, and keeps the
edit inside the 1 200-line `templates_manager.py` to about ten lines.

Module surface:

```python
GIT_DIFF_MAX_BYTES = 256_000
GIT_DIFF_STAT_WIDTH = 120
GIT_DIFF_STAT_MAX_FILES = 200
GIT_DIFF_EXCLUDED_TOP_LEVEL_DIRS = ("bench_out",)

def git_diff_pathspec() -> List[str]:
    """['--', ':(top,exclude)bench_out'] — one exclude per entry, all top-anchored."""

def bound_text(text: str, max_bytes: int) -> Tuple[str, bool, int]:
    """(kept, truncated, original_bytes). See D3."""

def capture_git_diff(wd: Path) -> Dict[str, Any]:
    """The four keys in D4, from two git commands run with cwd=wd. See D2."""
```

## D2. The two commands

Both run with `cwd=wd`, `encoding="UTF-8"`, through `subprocess.check_output`, exactly as
the capture does today. Both carry the same pathspec so the diff and the stat describe the
same set of files.

```
git diff --no-ext-diff --no-color -- :(top,exclude)bench_out
git diff --no-ext-diff --no-color --stat=120,,200 -- :(top,exclude)bench_out
```

`--no-ext-diff` and `--no-color` are hygiene. A developer's `diff.external` or
`color.ui=always` would otherwise land a tool's output or escape codes in an artifact. They
do not change the empty-when-clean property.

`--stat=<width>,<name-width>,<count>` bounds the stat structurally: at most 200 file lines,
each at most 120 columns, then a `...` line when files were dropped, then the
`N files changed` summary that git always keeps. Measured 2026-09-20: 300 changed files with
long paths gave 52 lines and 4 993 bytes at `--stat=100,,50`. The stat therefore needs no
truncation flag of its own. An empty middle parameter is accepted by git and means "default
name width".

## D3. The bounding rule for the unified diff

`bound_text(text, max_bytes)` returns `(kept, truncated, original_bytes)`:

1. `original_bytes = len(text.encode("utf-8"))`. Bytes, not characters, because the
   acceptance criterion is about serialized size.
2. If `text == ""`: return `("", False, 0)`. An empty diff stays empty. This is what keeps
   `deploy_git_dirty` `False` for a clean tree.
3. If `original_bytes <= max_bytes`: return `(text, False, original_bytes)`. A small diff is
   kept whole and reads exactly as today.
4. Otherwise keep the longest prefix of at most `max_bytes` bytes that ends at a newline.
   Implementation: `encoded.rfind(b"\n", 0, max_bytes)`; keep `encoded[: cut + 1]`.
5. If no newline falls inside the cap (one line longer than the cap), keep the longest
   character-safe prefix instead: `encoded[:max_bytes].decode("utf-8", errors="ignore")`.
   The result is non-empty for non-empty input, so a dirty tree can never read as clean
   because its first diff line was huge.

The kept text carries no in-band marker. The two keys in D4 are the contract; a marker
line would change the byte count a reader compares against the cap.

## D4. The recorded keys

`capture_git_diff(wd)` returns:

| key | type | value |
|---|---|---|
| `git_diff` | str | the bounded unified diff. `""` when the tree is clean or only top-level `bench_out/` changed |
| `git_diff_stat` | str | the `--stat=120,,200` output for the same files. `""` on the same conditions |
| `git_diff_truncated` | bool | `True` only when D3 step 4 or 5 cut the diff |
| `git_diff_original_bytes` | int | UTF-8 byte length of the full filtered diff before the cut. `0` when clean |

The key name `git_diff` is kept so the three readers in `src/utils/benchmark_provenance.py`
(`:499`, `:524`, `:609`) keep working unedited. The three new keys are flat siblings so the
YAML stays a one-level mapping like `last_commit` and `host`.

## D5. The exclusion

`:(top,exclude)bench_out` — long-form pathspec magic. `top` anchors the path at the
repository root regardless of `cwd`; `exclude` removes matches from the result. Git applies
an exclude-only pathspec to the whole tree, so no positive pathspec is needed, and adding
`.` as one would reintroduce the cwd trap the proposal measured. A pathspec that names a
directory matches everything under it, so deleted, modified and added files under
`bench_out/` are all dropped.

Only the **top-level** `bench_out/` is excluded. That is the shape on the FASRC checkout
(48 deletions of `bench_out/*.json`) and the only shape the issue names. A `bench_out`
directory nested elsewhere still counts as a change. Widening to "any path segment named
`bench_out`" is a future decision, not a side effect of this one.

## D6. The seam in `get_git_information`

```python
def get_git_information(wd: Optional[Path] = None) -> Dict[str, str]:
    meta_data: Dict[str, str] = {}
    wd = Path(wd) if wd is not None else Path(__file__).parent
    ...
    else:
        meta_data["last_commit"] = ...            # unchanged
        meta_data.update(capture_git_diff(wd))    # replaces the bare git diff
```

The default is the current behavior, so the one production caller at
`templates_manager.py:810` (`git_info = get_git_information()`) is untouched. The parameter
exists so tests can point the capture at a real throwaway repository and at a
**subdirectory** of it, which is the only way to prove D5's `top` anchoring. The
`-> Dict[str, str]` annotation is already violated by the `host` mapping (#433 left it
alone); leave it alone here too.

## D7. Tests

`tests/unit/test_benchmark_git_diff_bound.py`, a new file, so nothing is appended to an
existing test and no earlier test loses its last assertion.

The fixture builds a real repository under `tmp_path`: `src/pkg/mod.py`, `README.md`, and a
tracked `bench_out/art.json` of about 130 KB, committed once. Git identity is passed as
`-c user.name=t -c user.email=t@t` on the commit. The fixture sets `GIT_CONFIG_GLOBAL` to
`os.devnull` and `GIT_CONFIG_NOSYSTEM=1` through `monkeypatch.setenv` so the git processes
the **code under test** spawns cannot read the developer's own configuration. The module is
skipped when `git` is not on `PATH`; the loop container carries git 2.47.3 and the CI
runners run `pytest` directly on the checkout, so the skip never fires in the gate.

Do not assert on the `host` block in these tests. It is #433's contract and depends on the
container endpoint environment; `tests/unit/test_benchmark_host_provenance.py` owns it.

The dirty-flag invariant is verified through the real reader,
`src.utils.benchmark_provenance.code_version(sources=[...], deploy_git_info=<captured>)`,
not by re-implementing the `bool(strip())` rule in the test.

## D8. Rollout

Nothing to deploy from this change. `git_info.yaml` is written by `archi create` and frozen,
so a running deployment keeps its old file. The FASRC dev stack at `v2026.08.0` will keep
writing oversized artifacts until it is redeployed from a release that carries this fix; the
PR body says so.

## D9. Alternatives considered

- **`git status --porcelain` or `--stat` alone as the field.** Both preserve the
  empty-when-clean property, but they drop the diff content that makes a dirty deploy
  explainable months later. A bounded diff plus an unbounded-by-count stat keeps both.
- **Truncate silently.** A reader could not tell a 256 000-byte diff that was complete from
  one that was cut. The two marker keys cost nothing and remove the ambiguity.
- **Filter at the read side in `service_benchmark.add_metadata()`.** That would shrink new
  artifacts from old deployments, but the yaml on disk would still hold 33 MB and the fix
  would live in the image rather than at the source. Out of scope; noted for a follow-up if
  the FASRC redeploy is far off.
- **Rewrite `fasrc/archi-bench-out` history.** Forbidden by the issue.
