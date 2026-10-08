## Why

The base-image dependency guard (`tests/unit/test_base_image_dependency_compatibility.py`)
rejects valid pip syntax when a whole-line comment closes an open continuation buffer.
`_joined_lines` (`:295`) concatenates the buffer and the comment with no separator, so
`"vllm==0.9.0\\\n# note \\\ntorch==2.7.0\n"` yields `['vllm==0.9.0# note \\', 'torch==2.7.0']`.
`_COMMENT` (`:386`, `(^|\s+)#.*$`) needs whitespace before `#`, so the tail is not cut and the
anchored `_PIN_PATTERN` (`:271`) fails: `_parse_pins` (`:351`) returns only `{'torch': '2.7.0'}`.
The guard still fails closed, not open: `_parse_requirements` keeps `vllm` with the specifier
`'==0.9.0# note \\'`, so `_unpinned_protected` reports it and `_opaque_requirements` reports the
malformed logical line. pip reads the same text as `vllm==0.9.0`, so the result is a false
rejection of a valid file. Re-measured on 2026-10-08 at `origin/dev` `5564e016`.

pip 26.1.2's `join_lines` (`pip._internal.req.req_file`) prepends `" "` to a line only in its
`COMMENT_RE` branch; an ordinary continuation is a plain concatenation. The control
`"vllm==0.9.0\\\n--hash=sha256:aa\n"` is `vllm==0.9.0--hash=sha256:aa` in both pip and the guard.

## What Changes

- In `_joined_lines`, when a buffer is open and the incoming line matches `_COMMENT`, flush
  `buffered + " " + raw_line`. The non-comment path is unchanged.
- Add a test for the repro and a control test that a non-comment continuation still joins with
  no separator, so an unconditional separator cannot pass.
- Tests-only change: no `src/` file and no requirements file changes.

## Capabilities

### New Capabilities

### Modified Capabilities
- `dependency-pin-hygiene`: the base-image guard joins a comment that closes a continuation
  as pip does. (The capability lives only in unarchived change directories, so the delta uses
  ADDED.)

## Impact

- `tests/unit/test_base_image_dependency_compatibility.py` only.
- Out of scope: requirement-line parsing (options, specifiers, extras) — #530.
