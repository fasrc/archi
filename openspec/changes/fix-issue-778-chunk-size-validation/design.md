## Context

`VectorStoreManager.__init__` (`src/data_manager/vectorstore/manager.py:178-189` on
`origin/dev` `5f3d80e9`) reads `data_manager.chunking` and calls three resolvers:

- `_resolve_chunking_strategy` (`:66`): returns the strategy, default `sentence`.
- `_resolve_chunk_sizes` (`:53-63`): returns `(parent, child)` with
  `chunking_cfg.get(key, default)`. No type check.
- `_resolve_chunk_overlap` (`:78-93`): `None` → `CHILD_CHUNK_OVERLAP`; a bool, a non-`int`,
  or a negative value → `ValueError("data_manager.chunking.chunk_overlap must be a
  non-negative int, got ...")`.

All three run for every strategy, `character` included. The sizes go to
`build_hierarchical_nodes` (`:1016-1017`), and from there to `_clamped_overlap`
(`node_parsing.py:264`), `SentenceSplitter`, and the markdown packer (`:395`).

`effective_chunking` (`src/utils/ingest_provenance.py:90-143`) restates the manager's
resolution for the provenance snapshot. It must never raise. It clamps the overlap only
when all three values pass `_is_non_negative_int`; otherwise it reports them unclamped.

Nothing else in `src/` reads the two keys (`git grep parent_chunk_size origin/dev -- src`).

## Goals / Non-Goals

**Goals**
- A bool, a non-`int`, or a value below 1 for either size fails when the manager is built,
  with a `ValueError` that names the key and shows the bad value.
- An empty key (`None`) uses the default, for the manager and for `effective_chunking`.
- An absent key and a positive `int` behave exactly as before.

**Non-Goals**
- Changing `src/cli/templates/base-config.yaml` (#596 owns it) or `app.py`.
- Making `effective_chunking` refuse a bad size. It must never raise; it keeps reporting
  a refused size unclamped (existing tests
  `test_effective_chunking_invalid_parent_size_skips_clamping` and
  `test_effective_chunking_invalid_child_size_skips_clamping`).
- An upper bound on the sizes, or a rule that the child is not larger than the parent.
  The issue asks for neither.
- Validating the sizes only when the strategy is hierarchical. `chunk_overlap` is
  validated for every strategy today, and the issue asks for "the same way".

## Decisions

### D1. One per-key helper, the `chunk_overlap` rule with a floor of 1

```python
def _resolve_chunk_size(chunking_cfg, key, default):
    value = chunking_cfg.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            f"data_manager.chunking.{key} must be a positive int, got {value!r}"
        )
    return value
```

`_resolve_chunk_sizes` calls it for `parent_chunk_size` and `child_chunk_size`. The floor is
1, not 0 as for the overlap: a size of 0 gives the splitter no room for any token. The
message ends with `got {value!r}`, so `"512"` shows as `'512'` and the operator sees that it
is a string. A helper per key, not a loop, keeps the defaults explicit and the two keys
independent.

Alternative rejected: coerce a numeric string with `int(value)`. #596 made the render keep
the type on purpose; a silent coercion hides the config error that #596 made visible, and
the issue asks for a refusal.

### D2. `effective_chunking`: `None` → default, nothing else

```python
parent = chunking.get("parent_chunk_size")
if parent is None:
    parent = _DEFAULT_PARENT_CHUNK_SIZE
```

and the same for the child. This is the pattern of the overlap three lines below. With the
default in place, the existing clamp runs, so the reported overlap is the one that the
manager applies (scenario: parent `None`, child 30 → overlap 15; before this change it was
reported as 20).

### D3. Tests at the end of each test file, one constructor test

The resolver tests are appended at the end of
`tests/unit/test_vectorstore_manager_hierarchical.py`, after
`test_add_to_postgres_hierarchical_count_excludes_rolled_back_deletes`. One test builds a
real `VectorStoreManager` with `PostgresCatalogService` and
`ConfigService._resolve_embedding_classes` patched, so the refusal is proven on the
constructor path, not only on the helper. Before this change it fails with `DID NOT RAISE
ValueError`. The provenance tests are appended at the end of
`tests/unit/test_ingest_provenance.py`, after
`test_compare_legacy_row_with_non_default_dimensions_still_drifts`. All 7 test names are
new in both files on `5f3d80e9`.

## Measurement

Loop 1 ran each stage on a scratch copy of `origin/dev` `5f3d80e9`
(`git archive origin/dev src tests pyproject.toml docs`), with
`python -m pytest -q -o addopts="" tests/unit/test_vectorstore_manager_hierarchical.py
tests/unit/test_ingest_provenance.py` (101 items on `5f3d80e9`):

| Stage | Applied | Result |
|---|---|---|
| r1 | task 1.1 tests only | `14 failed, 102 passed` |
| g1 | task 1.1 tests + `manager.py` | `116 passed` |
| r2 | g1 + task 1.2 tests | `4 failed, 116 passed` |
| g2 | g1 + task 1.2 tests + `ingest_provenance.py` | `120 passed` |

In r1, the 14 failures are the 12 parametrized refusal items, the `None` test, and the
constructor test. `test_resolve_chunk_sizes_accepts_a_size_of_one` passes before and after;
it pins the floor. In r2, all 4 new provenance items fail.

Broken variants, each built on g2 (each must fail a named new test):

| Variant | Change | Result |
|---|---|---|
| m1 | drop the bool check | `2 failed` (`True` for both keys) |
| m2 | drop the `int` type check | `5 failed` (`'512'`, `2.5`, constructor) |
| m3 | floor `< 0` | `2 failed` (`0` for both keys) |
| m4 | floor `<= 1` | `1 failed` (`accepts_a_size_of_one`) |
| m5 | no `None` → default | `1 failed` (`null_resolves_each_size_to_its_own_default`) |
| m6 | validate the parent only | `8 failed` (6 child items, `None`, constructor) |
| m7 | provenance: parent `.get(key, default)` | `4 failed` |
| m8 | provenance: child `.get(key, default)` | `2 failed` |

`black` 24.10.0 and `isort` 6.0.1 leave the four changed Python files unchanged. Diff
coverage of the change against `5f3d80e9`: 15 lines, 0 missing, 100%.

## Risks / Trade-offs

- A deployment whose config holds a quoted, empty, or bool size today now stops at startup
  instead of failing (or chunking wrong) at ingest. That is the point of the issue. The
  error names the key, and the docs note says how to fix it.
- The chat service also builds `VectorStoreManager`, so a bad size stops it too. A bad
  `chunk_overlap` already does the same.
