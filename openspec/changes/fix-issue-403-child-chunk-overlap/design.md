# Design — configurable child-chunk overlap

## Context

Anchors on `origin/dev` `26e6429e`:

- `src/data_manager/vectorstore/node_parsing.py:58` — `CHILD_CHUNK_OVERLAP = 20`.
- `node_parsing.py:96-130` — `build_hierarchical_nodes(document, *, strategy,
  parent_chunk_size, child_chunk_size)` dispatches to `_parse_markdown` or
  `_parse_sentence`.
- `node_parsing.py:234-243` — `_clamped_overlap(chunk_size)` returns
  `min(CHILD_CHUNK_OVERLAP, max(chunk_size, 0))`.
- `node_parsing.py:257-260` — the sentence path:
  `HierarchicalNodeParser.from_defaults(chunk_sizes=[parent, child],
  chunk_overlap=_clamped_overlap(min(parent, child)))`. One overlap serves both levels.
- `node_parsing.py:514-518` — the markdown path: parent splitter `chunk_overlap=0` (keep),
  child splitter `chunk_overlap=_clamped_overlap(child_chunk_size)`.
- `src/data_manager/vectorstore/manager.py:40-50` — `_resolve_chunk_sizes(chunking_cfg)`;
  `:147-157` — `__init__` reads `chunking_cfg` and stores the sizes; `:908-913` — the only
  production call of `build_hierarchical_nodes`.
- `src/cli/templates/base-config.yaml:275-295` — the `chunking:` block; parent and child
  sizes use `{%- if ... is defined %}`.
- `scripts/benchmarking/measure_chunk_overlap.py:312-314` — `clamp_overlap(overlap,
  chunk_size, parent_chunk_size)` returns `max(0, min(overlap, chunk_size,
  parent_chunk_size))` and claims to match production.

## Decisions

### D1. One key, `data_manager.chunking.chunk_overlap`, default 20

The key sits in the `chunking:` block beside `parent_chunk_size` and `child_chunk_size`. The
default stays the constant `CHILD_CHUNK_OVERLAP`, which remains the single source of the
default. #489 will read the same key name.

### D2. The clamp is `min(overlap, chunk_size // 2)`

`_clamped_overlap` takes the configured overlap as a second parameter:
`_clamped_overlap(chunk_size, overlap=CHILD_CHUNK_OVERLAP)` returns
`max(0, min(overlap, chunk_size // 2))`. Both call sites pass the configured value. The
sentence path keeps `min(parent_chunk_size, child_chunk_size)` as the size it clamps
against, because `HierarchicalNodeParser` applies one overlap to both levels. The operator
chose this rule on 2026-09-26 (issue #403 body, "Decision").

Consequence: a default-overlap child size below 40 now gets less than 20. The existing test
`test_clamped_overlap_boundary_values` (`tests/unit/test_node_parsing.py:620-628`) pins the
old rule (`_clamped_overlap(20) == 20`, `_clamped_overlap(21) == 20`). Update it to the new
values in the same task that changes the clamp; that is the intended behavior change, not a
regression.

### D3. `build_hierarchical_nodes` gets `child_chunk_overlap: int = CHILD_CHUNK_OVERLAP`

A keyword parameter with the constant as default keeps every current caller (tests and
`scripts/benchmarking/measure_chunk_overlap.py`) valid. `_parse_sentence` and
`_parse_markdown` each get the value as a new positional parameter.

### D4. The manager validates the value once, at construction

A new resolver `_resolve_chunk_overlap(chunking_cfg)` in `manager.py` returns
`chunking_cfg.get("chunk_overlap", CHILD_CHUNK_OVERLAP)`. `__init__` stores it as
`self.child_chunk_overlap` and the call at `manager.py:908-913` passes it. The resolver
raises `ValueError` naming `data_manager.chunking.chunk_overlap` when the value is a bool, is
not an `int`, or is negative. `None` (a key present but empty) resolves to the default, the
same as an absent key.

Why: the clamp hides a large value but not a negative or non-integer one. LlamaIndex would
then raise inside every document's parse, which the ingest logs per file. One error at
startup is clearer. A bool is an `int` subclass in Python, so it must be rejected
explicitly; `true` would otherwise become overlap 1.

### D5. Render only when set, keep `0`

```jinja
{%- if data_manager.chunking.chunk_overlap is defined and data_manager.chunking.chunk_overlap is not none %}
    chunk_overlap: {{ data_manager.chunking.chunk_overlap | tojson }}
{%- endif %}
```

`| tojson`, never a bare interpolation: YAML reloads a bare `"20"` as `20` and `"null"`
as `None`, so an invalid string would pass `_resolve_chunk_overlap` instead of failing.

Never `| default(20, true)`: the truthy default turns a configured `0` into `20`, and the
`0` arm is the one the sweep needs.

### D6. The measurement script follows production

`clamp_overlap` in `scripts/benchmarking/measure_chunk_overlap.py` becomes
`max(0, min(overlap, min(chunk_size, parent_chunk_size) // 2))`, and the module docstring
(line 23) names the new rule. A parity test in `tests/unit/test_measure_chunk_overlap.py`
asserts `clamp_overlap` equals `_clamped_overlap` over a grid of sizes and overlaps,
including sizes below 40. Without it, the script reports an "effective overlap" that the
ingest does not use.

## Risks

- **Small child sizes change.** Covered by D2; only sizes below 40 move, and the operator
  chose the rule to remove near-duplicate chunks there.
- **No re-chunk on change.** A new overlap affects only documents ingested after it. The
  docs say so, as they do for `strategy`.

## Verification

Unit tests only. The sweep that uses the knob is `needs-deploy` and belongs to #396.
