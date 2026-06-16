## Context

`scripts/dev/run_smoke_preview.sh` is a ~500-line bash script that orchestrates smoke test deployments. It contains 17 inline Python heredoc blocks (`<<'PY'`) that manipulate YAML config files and validate agent specs. These blocks account for roughly 40% of the script's line count and are the primary source of merge conflicts when the vLLM and ollama provider branches diverge.

The inline blocks fall into distinct categories:
- 12 blocks that read a single value from a YAML file via a dotted path
- 2 blocks that write provider config (ollama, vLLM) back into YAML
- 1 block that validates agent spec files by importing `agent_spec.py`
- 1 block that parses a `.env` file for a key
- 1 block that reads the vLLM default model from a different config path

## Goals / Non-Goals

**Goals:**
- Extract all inline Python into a single standalone script with proper editor support
- Preserve exact behavioral parity — same inputs, outputs, exit codes
- Make the Python logic independently testable with pytest
- Reduce merge conflict surface in the shell script

**Non-Goals:**
- Changing the smoke test workflow or adding new capabilities to it
- Replacing bash with Python for the orchestration logic (deployment, cleanup, health checks)
- Making the helper a general-purpose CLI tool beyond what the smoke runner needs
- Adding the helper as an installable package or entry point

## Decisions

### Single file with argparse subcommands over multiple scripts
The helper is one file (`scripts/dev/smoke_config_helper.py`) with subcommands rather than 5 separate scripts.

**Rationale:** The operations share common YAML loading logic. A single file with `get`, `set-ollama`, `set-vllm`, `validate-agents`, `env-get` subcommands keeps imports centralized and is easier to discover. The alternative (separate files per operation) would scatter related logic and require the shell script to know 5 different script paths.

### Dotted path navigation for YAML reads over explicit subcommands per field
`get <file> <path> --default <val>` handles all 12 read operations with a single subcommand using dot-separated keys (e.g., `services.postgres.host`).

**Rationale:** The 12 read blocks are structurally identical — open YAML, traverse nested dicts, print a leaf value. A generic getter eliminates duplication. Array indexing via numeric path segments (e.g., `services.chat_app.providers.local.models.0`) covers the one case that reads a list element.

### Environment variables replaced by CLI arguments
The current inline blocks pass values via environment variables (`CONFIG_DEST`, `RENDERED_CONFIG`, etc.). The helper takes these as positional/flag arguments instead.

**Rationale:** CLI arguments are explicit, self-documenting via `--help`, and don't pollute the shell environment. The shell script already has the values in variables — passing them as arguments is a trivial change.

### validate-agents imports agent_spec.py dynamically
The `validate-agents` subcommand uses `importlib.util` to load `src/archi/pipelines/agents/agent_spec.py` at runtime, same as the current heredoc.

**Rationale:** The smoke runner doesn't require archi to be installed (`pip install -e .`), so a direct import of `archi.pipelines.agents.agent_spec` may not be available. Dynamic loading from a file path keeps the same zero-install assumption.

## Risks / Trade-offs

- **[Python not on PATH]** → The script already calls `python` 17 times; no new risk. The helper uses the same `python` invocation.
- **[Behavioral drift during rewrite]** → Mitigation: each subcommand is a mechanical extraction of the existing heredoc logic. Tests verify output matches for known config fixtures.
- **[Helper becomes a maintenance burden]** → Mitigation: it's scoped to smoke test support only, not a general tool. No public API contract beyond what the shell script calls.
