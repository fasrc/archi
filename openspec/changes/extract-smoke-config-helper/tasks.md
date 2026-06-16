## 1. Create helper script scaffold

- [ ] 1.1 Create `scripts/dev/smoke_config_helper.py` with argparse setup and the five subcommand stubs (`get`, `set-ollama`, `set-vllm`, `validate-agents`, `env-get`)
- [ ] 1.2 Implement `get` subcommand — dotted path navigation with `--default`, array index support, error handling for missing files

## 2. Implement config writing subcommands

- [ ] 2.1 Implement `set-ollama` subcommand — write model/url into `services.chat_app.providers.local`, handle partial args
- [ ] 2.2 Implement `set-vllm` subcommand — write base-url/model into `archi.providers.vllm`, handle partial args

## 3. Implement validation and env parsing subcommands

- [ ] 3.1 Implement `validate-agents` subcommand — read `agents_dir` from config, dynamically import `agent_spec.py`, list and load all `.md` files, error on missing dir/files/module
- [ ] 3.2 Implement `env-get` subcommand — parse `.env` file for a key, print value or empty string

## 4. Refactor shell script

- [ ] 4.1 Replace the 12 YAML-read heredoc blocks in `run_smoke_preview.sh` with `get` subcommand calls
- [ ] 4.2 Replace the `set-ollama` heredoc block with a `set-ollama` call
- [ ] 4.3 Replace the `set-vllm` heredoc block with a `set-vllm` call
- [ ] 4.4 Replace the `validate-agents` heredoc block with a `validate-agents` call
- [ ] 4.5 Replace the `env-get` heredoc block with an `env-get` call
- [ ] 4.6 Verify zero `<<'PY'` or `<<PY` patterns remain in the shell script

## 5. Testing

- [ ] 5.1 Add pytest tests for `get` subcommand (nested read, array index, missing key with/without default, missing file)
- [ ] 5.2 Add pytest tests for `set-ollama` and `set-vllm` (full args, partial args, round-trip YAML integrity)
- [ ] 5.3 Add pytest tests for `validate-agents` (valid dir, missing dir, no .md files, missing agent_spec.py)
- [ ] 5.4 Add pytest tests for `env-get` (key exists, key missing, file missing)
- [ ] 5.5 Run `bash -n` on refactored `run_smoke_preview.sh` to verify syntax
