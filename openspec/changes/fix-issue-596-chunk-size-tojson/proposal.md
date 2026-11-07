## Why

The config template interpolates the two hierarchical chunk sizes bare (issue #596). On `origin/dev` `6b8afe98`:

- `src/cli/templates/base-config.yaml:358`: `parent_chunk_size: {{ data_manager.chunking.parent_chunk_size }}`.
- `src/cli/templates/base-config.yaml:361`: `child_chunk_size: {{ data_manager.chunking.child_chunk_size }}`.
- `src/cli/templates/base-config.yaml:366`: `chunk_overlap: {{ data_manager.chunking.chunk_overlap | tojson }}` (#595, #403) is the pattern to copy.

When the rendered YAML is loaded again, YAML 1.1 changes the type of a quoted value. Measured on `6b8afe98` with the render helper of `tests/unit/test_base_config_chunking_render.py`: `parent_chunk_size: "2048"` and `child_chunk_size: "512"` reload as the integers `2048` and `512`. The strings `"null"` and `"~"` reload as `None`, and `"true"`, `"on"`, `"yes"` reload as booleans. So the runtime config does not hold the value that the operator wrote.

The operator narrowed the issue on 2026-10-20 to this template fix. The other half of the original issue (the two sizes in the ingest-config snapshot) is already met by `effective_chunking` (`src/utils/ingest_provenance.py`, #619).

## What Changes

- `src/cli/templates/base-config.yaml`: render `parent_chunk_size` and `child_chunk_size` with `| tojson`. Keep the two `{%- if ... is defined %}` guards as they are, so that an unset size is still left out and `VectorStoreManager` uses its defaults (2048 and 512). Update the comment above the two lines to say why `tojson` is there.
- `tests/unit/test_base_config_chunking_render.py`: new render tests. A quoted size keeps its value and the type `str`. An integer size keeps the type `int`. An unset size stays absent.
- `docs/docs/configuration.md`: one sentence in the `chunking` section. Give both sizes as integers; a quoted value now stays a string, and the hierarchical parser cannot use it.

Out of scope (issue #596): any change to `src/utils/ingest_provenance.py` or to the snapshot keys (met by #619), and a type check for the two sizes in `_resolve_chunk_sizes` (`src/data_manager/vectorstore/manager.py:53`). Design D2 gives the reason; the follow-up is #778.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `hierarchical-rerank-retrieval`: one ADDED requirement. The config render keeps the type of a configured `parent_chunk_size` and `child_chunk_size`.

## Impact

- Code: `src/cli/templates/base-config.yaml` (2 lines and 1 comment).
- Tests: `tests/unit/test_base_config_chunking_render.py` (new tests, appended at the end of the file).
- Docs: `docs/docs/configuration.md` (`chunking` section).
- Runtime: an integer size renders the same text as before (`tojson` of `1024` is `1024`). An unset size renders nothing, as before. Only a quoted (string) size changes: it now reaches `VectorStoreManager` as a string (design D2).
