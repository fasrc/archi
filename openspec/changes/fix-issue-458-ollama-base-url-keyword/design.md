## Context

`_get_ollama_model` (`src/archi/providers/local_provider.py:138-161`) builds:

```python
model_kwargs = {"model": ..., "streaming": True, **self.config.extra_kwargs, **kwargs}
model_kwargs.pop("local_mode", None)
if self.config.base_url:
    model_kwargs["base_url"] = self.config.base_url
```

The last assignment overwrites a caller's `base_url=`. `_get_openai_compat_model` puts
`"base_url": base_url` first and spreads `**kwargs` last, so the keyword wins there.

## Decision

Keep the configured value as the fallback, but let a call keyword win. Read the keyword
from `kwargs` (the call), not from the merged `model_kwargs`:

```python
if "base_url" in kwargs:
    model_kwargs["base_url"] = kwargs["base_url"]
elif self.config.base_url:
    model_kwargs["base_url"] = self.config.base_url
```

Reading from `kwargs` keeps the prior behavior for an `extra_kwargs["base_url"]`: the
configured endpoint still overwrites it. A plain `setdefault` on `model_kwargs` would let
an `extra_kwargs` value win, which is a behavior change the issue does not ask for.

The keyword is passed through as given, the same as in `openai_compat` mode. It is not
normalized (no `http://` prefix). This matches the `openai_compat` builder, which the issue
names as the behavior to match.

## Tests

Reuse the `OLLAMA_HOST` monkeypatch pattern in
`tests/unit/test_local_provider_env_override.py`, and patch `langchain_ollama.ChatOllama`
with a recorder (`monkeypatch.setattr("langchain_ollama.ChatOllama", Recorder)`).

1. With `OLLAMA_HOST` set and a configured `base_url`, `_get_ollama_model("m",
   base_url="http://other:11434")` gives the recorder `base_url="http://other:11434"`.
   This test fails before the change.
2. With no keyword, the recorder gets `provider.config.base_url`.
3. With `extra_kwargs={"local_mode": "ollama", "base_url": "http://extra:1"}` and no
   keyword, the recorder gets `provider.config.base_url` (prior behavior kept).
