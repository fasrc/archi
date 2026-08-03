## Why

An `ImportError` raised while constructing a request-time provider override is the one override
failure that reaches the caller as **silence** — no `error` event, no `warning` event. The request
falls back to the default model and returns a normal-looking answer, so a client that asked for
`provider/model` cannot tell from the response that a different model answered. Because provider
SDKs are imported lazily inside `_ensure_providers_registered()`, a deployment missing or
misinstalling an optional dependency hits this at request time rather than at startup: it is a
live configuration failure mode, and silent degradation is the response most likely to leave the
misconfiguration in place.

The fallback itself is correct. The defect is that it is invisible.

## What Changes

- `_create_provider_llm` (`src/interfaces/chat_app/app.py:1610`) stops swallowing `ImportError`.
  Its `except ImportError` clause at `:1645-1647` currently logs and `return None`, while the
  adjacent `except Exception` at `:1648-1650` logs and re-raises — the two disagree. `ImportError`
  will propagate like every other construction failure.
- No new branch is added at the call site. `ChatWrapper.stream` (`:2094`) already routes a
  non-`ValueError` exception to `except Exception` (`:2104-2109`), which logs, emits
  `{"type": "warning", "message": "Using default model: …"}`, and sets `override_llm = None`.
  Reaching that existing handler *is* the fix.
- New tests cover both halves: a direct test of the real `_create_provider_llm` body asserting
  `ImportError` propagates instead of returning `None`, and an end-to-end test through `stream`
  asserting the `warning` event is emitted and the default pipeline answers.
- `docs/docs/api_reference.md:210` — the override outcome table's "**nothing at all**" row names
  `ImportError` and `app.py:1645` as a current silent path. That half of the row is removed; the
  no-`agent_llm` half (`app.py:2111`) stays, because it is a separate silent path and out of scope.

Not breaking: no public HTTP contract changes. An override that failed on `ImportError` returned a
default-model answer before and still does — it now also carries a `warning` event.

## Capabilities

### New Capabilities
- `chat-override-failure-signalling`: every request-time provider/model override failure is
  announced to the caller on the response stream — no failure mode degrades silently to the
  default model.

### Modified Capabilities
<!-- None. The request-local semantics of the override (capability
     `request-local-llm-override`, change fix-issue-86) are unchanged: this change alters only
     how a construction failure is *reported*, not what the override does when it succeeds or
     what state a failure touches. -->

## Impact

- **Code**: `src/interfaces/chat_app/app.py` — deletion of the `except ImportError` clause at
  `:1645-1647` (or its replacement with a log-and-`raise` that preserves the specific
  "Providers module not available" message). Behaviour change is confined to
  `_create_provider_llm`, which has exactly one caller (`:2094`, verified).
- **Contract**: the `None` return of `_create_provider_llm` on `ImportError` is removed. Nothing
  else consumes that return — the sole caller's guard at `:2111` treats falsey as "no override",
  which is precisely the silent path being closed.
- **Tests**: `tests/unit/test_chat_override_persistence.py`. All four existing
  `_create_provider_llm` references in `tests/` are seam substitutions that replace the method,
  so the real body is executed by no test today; the direct test is new coverage, not a change to
  existing coverage.
- **Docs**: `docs/docs/api_reference.md` override outcome table (`:210`) and its `[ovrimport]`
  link reference (`:228`).
- **Unchanged, deliberately**: the `ValueError` → HTTP `400` early-return path (`:2097-2103`) and
  the generic `except Exception` → warning path (`:2104-2109`). Both are correct and tested; the
  fix must not collapse the `400` into a warning.
- **Dependencies / APIs / deployment**: none.
