## Context

`archi create` renders `src/cli/templates/base-config.yaml` with Jinja, and the containers load the result with `yaml.safe_load`. A bare `{{ x }}` writes the text of the value without quotes, so YAML 1.1 reads it again and can change its type. `| tojson` writes a JSON scalar. A JSON scalar is valid YAML and carries its own type, so a string stays a string and an integer stays an integer.

`chunk_overlap` already uses `| tojson` (`base-config.yaml:366`, from #595). The two size keys at `:358` and `:361` do not.

## Facts the issue body does not state

### D1. The anchors moved

The issue gives `:344` and `:347` (on `82850b3b`). On `origin/dev` `6b8afe98` the two lines are `:358` and `:361`, and the `chunk_overlap` line is `:366`. Find the lines by their text, not by number.

### D2. No validator reads the two sizes, so a quoted size now fails later

`_resolve_chunk_overlap` (`src/data_manager/vectorstore/manager.py:78`) refuses a non-integer `chunk_overlap` with one clear `ValueError` when `VectorStoreManager` is built. `_resolve_chunk_sizes` (`manager.py:53`) has no such check. It returns the two values as they are.

Measured on `6b8afe98`: today `parent_chunk_size: "2048"` reloads as the integer `2048`, so the ingest works. After this change it reaches the node parser as the string `"2048"`. On the `sentence` strategy, `_clamped_overlap` (`src/data_manager/vectorstore/node_parsing.py:264`) then raises `TypeError: unsupported operand type(s) for //: 'str' and 'int'`. The `markdown` strategy compares a token count with the size at `node_parsing.py:395`, which also fails for a string.

The operator narrowed issue #596 to the template fix on 2026-10-20. So this change does NOT add a check to `_resolve_chunk_sizes`. The follow-up issue for that check is #778, and the PR body links it. No shipped config quotes a size: `examples/`, `deploy/fasrc-dev/config.example.yaml`, and `docs/` give integers or leave the keys out, and `docs/docs/configuration.md:646-647` documents the type as `int`.

### D3. The frozen default-filter baseline does not change

`tests/unit/test_base_config_falsy_defaults_render.py` freezes every `default(` call site in the template (`_NON_BOOL_DEFAULT_BASELINE`). The two size lines have no `default(` filter before or after this change, so that baseline and its tests do not change. Do not edit that file.

### D4. A size of `None`

A key that is present with no value (`parent_chunk_size:`) gives `None`. The guard is `is defined`, and `None` is defined, so the line is still rendered. Before this change it rendered the text `None`, which reloads as the string `"None"`. After it, `tojson` renders `null`, which reloads as `None`. Both values fail in the node parser, so this change does not make the `None` case worse. The issue says to keep the guards, so this change does not add `is not none`. Do not write a test that pins the `None` result.

## Goals / Non-Goals

Goals: a configured string size keeps its value and type `str` after the render and reload; an integer size keeps type `int`; an unset size stays absent.

Non-goals: a type check in `_resolve_chunk_sizes` (D2); a change to the guards (D4); any change to `src/utils/ingest_provenance.py`.

## Decisions

### E1. Template (`src/cli/templates/base-config.yaml`)

Change exactly the two value lines:

```
    parent_chunk_size: {{ data_manager.chunking.parent_chunk_size | tojson }}
    child_chunk_size: {{ data_manager.chunking.child_chunk_size | tojson }}
```

Keep the two `{%- if data_manager.chunking.<key> is defined %}` and `{%- endif %}` lines unchanged. Add one comment line to the comment block above the two guards (the block that starts `# Target parent/child node sizes`), in the same words as the `chunk_overlap` comment: `tojson` keeps the configured type, so a quoted value does not reload as an int. A `#` comment line inside the template is safe here: the block above already has three.

### E2. Tests (`tests/unit/test_base_config_chunking_render.py`)

Append the new tests at the END of the file. Use the `_render` helper already in the file. Each test has a new, unique name (`grep -n "def test_<name>" tests/unit/` prints one line).

1. `test_chunk_sizes_string_keeps_its_type`, parametrized over `"2048"`, `"512"`, `"null"`, `"Null"`, `"NULL"`, `"~"`, `"on"`, `"off"`, `"yes"`, `"no"`, `"true"`. Render with both `parent_chunk_size` and `child_chunk_size` set to the value. For each key, assert that the reloaded value equals the value AND that `type(...) is str`.
2. `test_chunk_sizes_int_keeps_int_type`: render `parent_chunk_size: 1024`, `child_chunk_size: 256`. Assert the two values and `type(...) is int` for each (an equality check alone passes for `True == 1`).
3. `test_chunk_sizes_mixed_set_and_unset`: render only `child_chunk_size: "512"`. Assert `child_chunk_size == "512"` with type `str`, and that `parent_chunk_size` is not in the chunking section.

The existing `test_chunk_sizes_absent_when_unset_preserves_defaults` already covers "both unset → both absent"; it does not change.

Red before the fix: case 1 fails for every value (`"2048"` reloads as `2048`, `"null"` as `None`, `"on"` as `True`), and case 3 fails (`"512"` reloads as `512`). Case 2 passes before and after; it is a guard against a regression.

### E3. Docs (`docs/docs/configuration.md`, `chunking` section)

Add one sentence to the backward-compatibility note under the example YAML block (the note that starts `> **Backward compatibility:**`), or directly under the table: give `parent_chunk_size` and `child_chunk_size` as integers; a quoted value such as `"512"` stays a string after the render, and the hierarchical parser cannot use it. Do not say that the value is refused with a clear error; that is not true (D2).

## Test oracle (issue #596 acceptance criteria)

| Criterion | Proof |
|---|---|
| `child_chunk_size: "512"` gives the string `"512"`; same for `parent_chunk_size: "2048"` | E2 tests 1 and 3 |
| Integer values still reload as integers | E2 test 2 |
| Unset sizes are still absent | `test_chunk_sizes_absent_when_unset_preserves_defaults` (existing) and E2 test 3 |
| `bash scripts/gate.sh` exits 0 | the gate on the branch tip |

## Risks / Trade-offs

- A quoted size that works today by accident fails at ingest after this change (D2). No shipped config quotes a size, and the docs give the type as `int`. Issue #778 adds the clear refusal that `chunk_overlap` already has.
- A key present with no value keeps failing, as before (D4).
