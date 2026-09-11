## 1. Scope the endpoint resolution to the local mode

- [x] 1.1 Make `OLLAMA_HOST` and the fallback endpoint mode-aware in one commit.

  Do the steps in this order. The task ends green and commits; do not stop while the
  suite is red.

  1. Create `tests/unit/test_local_provider_env_override.py`. Build the provider
     directly: `LocalProvider(ProviderConfig(provider_type=ProviderType.LOCAL,
     base_url=..., extra_kwargs={"local_mode": ...}))`, then assert on
     `provider.config.base_url`. Use `monkeypatch.setenv` and
     `monkeypatch.delenv("OLLAMA_HOST", raising=False)` in every case, so no case leaks
     the variable into another. Give every test a unique `def test_...` name — the gate
     runs no linter, so a reused name silently drops the earlier test. Cover ten cases:

     | `OLLAMA_HOST` | configured `base_url` | mode | expected `config.base_url` |
     |---|---|---|---|
     | `http://ollama-box:11434` | `http://gpu-vllm:8000/v1` | `openai_compat` | `http://gpu-vllm:8000/v1` — **fails today** |
     | `http://ollama-box:11434` | `http://gpu-vllm:8000/v1` | `ollama` | `http://ollama-box:11434` |
     | `http://ollama-box:11434` | absent | `openai_compat` | `http://localhost:8000/v1` — **fails today** |
     | `http://ollama-box:11434` | absent | `ollama` | `http://ollama-box:11434` |
     | unset | `http://gpu-vllm:8000/v1` | `openai_compat` | `http://gpu-vllm:8000/v1` |
     | unset | `http://gpu-vllm:8000/v1` | `ollama` | `http://gpu-vllm:8000/v1` |
     | unset | absent | `openai_compat` | `http://localhost:8000/v1` — **fails today** |
     | unset | absent | `ollama` | `http://localhost:11434` |
     | `""` (empty) | `http://gpu-vllm:8000/v1` | `openai_compat` | `http://gpu-vllm:8000/v1` |
     | unset | `gpu-vllm:8000/v1` | `openai_compat` | `http://gpu-vllm:8000/v1` |

     Reference the class constants (`LocalProvider.DEFAULT_OPENAI_COMPAT_BASE_URL`,
     `LocalProvider.DEFAULT_OLLAMA_BASE_URL`) rather than repeating the literals, and
     record in the docstring of the absent-`base_url` cases that #450 chose the
     openai-compat default over the Ollama host.

  2. Run `PATH=/home/austin/miniforge3/envs/archi/bin:$PATH python -m pytest
     tests/unit/test_local_provider_env_override.py -q`. Confirm exactly the three cases
     marked **fails today** fail, and that the other seven pass. If a different set
     fails, stop and re-read the resolution branch before editing it.

  3. Edit the `else` branch of `LocalProvider.__init__`
     (`src/archi/providers/local_provider.py:70-76`). Read the mode off the incoming
     object with `config.extra_kwargs.get("local_mode", "ollama")` — not the
     `self.local_mode` property, which reads `self.config` and is unset until
     `super().__init__(config)` on line 77. Then:
     - in `ollama` mode keep today's order exactly: `OLLAMA_HOST`, else the configured
       `base_url`, else `DEFAULT_OLLAMA_BASE_URL`;
     - in any other mode ignore `OLLAMA_HOST` entirely and use the configured `base_url`,
       else `DEFAULT_OPENAI_COMPAT_BASE_URL`.
     Leave the `config is None` branch alone: it hard-codes `local_mode: "ollama"`, so the
     mode-aware rule already admits the override there. Keep the
     `config.base_url = self._normalize_base_url(config.base_url)` call last, so every
     resolved value still gets a scheme.

  4. Re-run the new file. All ten cases pass.

  5. Update the two tests in `tests/unit/test_ragas_evaluator_local_mode.py` that pin the
     old fallback. Change the expected endpoint in each to `http://localhost:8000/v1`, and
     add one docstring line saying #450 moved it and why:
     - `test_local_openai_compat_judge_without_a_url_keeps_the_provider_default`
       (was `http://localhost:11434`) — the provider default for this mode is now the
       openai-compat one, so the assertion tracks the mode instead of the Ollama port.
     - `test_local_openai_compat_judge_without_a_url_still_inherits_ollama_host`
       (was `http://sut-host:9000/v1`) — a URL-less judge no longer inherits the system
       under test, which is the self-scoring path the same file's other docstrings call a
       hijack. Rename it to
       `test_local_openai_compat_judge_without_a_url_ignores_the_sut_ollama_host` so the
       name stops asserting the removed behavior, and confirm no other test in the file
       already uses that name.
     Do not touch the other twelve tests in the file: they pass an explicit `base_url`
     keyword that already outranks the environment.

  6. Run the regression set named in the issue:
     `PATH=/home/austin/miniforge3/envs/archi/bin:$PATH python -m pytest
     tests/unit/test_ragas_evaluator_local_mode.py tests/unit/test_get_model_local_mode.py
     tests/unit/test_provider_config_override.py tests/unit/test_benchmark_sut.py -q`.
     It must exit 0.

  7. Prove the tests bind to the defect: revert only the source edit, confirm the three
     marked cases fail again, then restore the edit.

  8. Format before you stage — the pre-commit hook's black is a writer while CI's is an
     assert, so a commit can be pushed misformatted. Run `bash scripts/gate.sh` bare; do
     not pipe or redirect it, and never pass `--no-verify`. Confirm `git status` is empty
     after the commit. No `Co-Authored-By` trailer.

## 2. Correct the comments that describe the removed behavior

- [ ] 2.1 Fix the two stale `OLLAMA_HOST` comments in `src/bin/service_benchmark.py`.

  `src/bin/service_benchmark.py:1420-1425` and `:1447-1450` both state as current fact
  that `LocalProvider` overwrites `config.base_url` with `OLLAMA_HOST`. After task 1 that
  is false in `openai_compat` mode, and both comment blocks sit on `openai_compat` arms.

  - Rewrite each block to say the provider now honors the configured `base_url` in
    `openai_compat` mode, so the keyword agrees with the config copy instead of
    outranking it.
  - Keep both `base_url` arguments and the `override` guard exactly as they are. The
    keyword is redundant after task 1, not wrong; removing it changes judge behavior and
    is out of scope. Do not write that the keyword is unnecessary.
  - Comment text only. Change no statement, so the diff adds no coverable line.
  - Run `bash scripts/gate.sh` bare and commit. Confirm `git status` is empty after.
