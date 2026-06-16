## Why

`scripts/dev/run_smoke_preview.sh` contains 17 inline Python heredoc blocks (`<<'PY'`) that read/write YAML config and validate agent specs. These blocks get no syntax highlighting, no linting, and no test coverage in editors. They are also the primary source of merge conflicts — the heredoc boundaries break diff alignment, and every recent merge into the vLLM branch has corrupted the if/fi nesting around them.

## What Changes

- **New standalone helper**: `scripts/dev/smoke_config_helper.py` with argparse subcommands replacing all 17 inline Python blocks:
  - `get <file> <dotted.path> [--default]` — read a single value from YAML (replaces 12 blocks)
  - `set-ollama <file> --model --url` — write ollama provider config (replaces 1 block)
  - `set-vllm <file> --base-url --model` — write vLLM provider config (replaces 1 block)
  - `validate-agents <file> --repo-root` — load and validate agent spec files (replaces 1 block)
  - `env-get <file> <key>` — parse a `.env` file for a key value (replaces 1 block)
- **Modified shell script**: `scripts/dev/run_smoke_preview.sh` replaces all heredocs with one-liner calls to the helper. No behavioral changes — same inputs, same outputs, same exit codes.

## Capabilities

### New Capabilities
- `smoke-config-helper`: Standalone Python CLI for reading/writing YAML config and validating agent specs, used by the smoke test runner.

### Modified Capabilities
<!-- No existing specs to modify -->

## Impact

- **Files changed**: `scripts/dev/run_smoke_preview.sh` (rewrite heredoc sections), new `scripts/dev/smoke_config_helper.py`
- **Dependencies**: None new — uses only `yaml`, `argparse`, `pathlib`, `importlib.util` (all already available in the environment)
- **Testing**: The helper becomes independently testable with pytest; the shell script behavior is unchanged
- **CI**: No pipeline changes — `run_smoke_preview.sh` is called the same way
