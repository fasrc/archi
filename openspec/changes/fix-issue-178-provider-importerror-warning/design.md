## Context

`ChatWrapper._create_provider_llm` (`src/interfaces/chat_app/app.py:1610`) builds the LLM for a
request-time provider/model override. Its body opens with a lazy import — `from
src.archi.providers import get_provider` (`:1623`) — inside the same `try` that wraps
construction. That import is what makes `ImportError` a request-time event rather than a startup
event: a deployment missing or half-installing an optional provider SDK raises here, on the
request that asks for that provider.

The method's two exception clauses disagree about what failure means:

```
1645        except ImportError as e:
1646            logger.warning(f"Providers module not available: {e}")
1647            return None          # swallowed
1648        except Exception as e:
1649            logger.warning(f"Failed to create provider LLM {provider}/{model}: {e}")
1650            raise                # propagated
```

The method has exactly one caller, in `ChatWrapper.stream` (`:2094`), which handles a raise but
has no branch for `None`:

- `except ValueError` (`:2097`) → `{"type": "error", "status": 400}` and `return`.
- `except Exception` (`:2104`) → logs, emits `{"type": "warning", "message": "Using default
  model: …"}`, sets `override_llm = None`.
- The override is then applied only under `if override_llm and hasattr(...)` (`:2110-2114`).

So on `ImportError` nothing raises, the guard evaluates falsey, and control falls through to the
default pipeline having emitted no event at all. The caller receives a normal-looking answer from
a model it did not ask for.

Constraints: `app.py` is a large file with a black-reflow churn risk, the repo gate requires ≥80%
diff coverage on changed lines, and the real body of `_create_provider_llm` is currently executed
by **no** test — all four `_create_provider_llm` references in `tests/` replace the method with a
substitute so the *caller* can be driven.

## Goals / Non-Goals

**Goals:**

- Every override construction failure, `ImportError` included, reaches the client as an in-band
  `error` or `warning` event.
- Close the gap by routing `ImportError` into the call site's existing handler rather than adding
  a branch — no new semantics, no new event type.
- Put the real `_create_provider_llm` body under test for the first time, at the specific
  contract being changed.
- Stop the API reference from documenting a fixed bug as current behaviour.

**Non-Goals:**

- Making `ImportError` fatal to the request. Falling back to the default model is correct; only
  the silence is the defect.
- Changing the `ValueError` → `400` path or the generic `except Exception` → warning path. Both
  are correct and tested.
- Fixing the *other* silent path in the same documented row — an active pipeline exposing no
  `agent_llm` (`:2111`). Separate defect, out of scope, stays documented.
- Restructuring the override block, the lazy import, or provider registration.

## Decisions

### D1: Delete the `except ImportError` clause outright rather than log-and-raise

The alternative was to keep the clause and change `return None` to `raise`, preserving the
"Providers module not available" wording. Deleting wins because the generic message is a strict
superset of the specific one: both interpolate `{e}` — which carries the actionable content (`No
module named 'anthropic'`) — and the generic message *also* names the provider and model that
were requested. Keeping the clause would cost a branch and log the same failure twice (once at
`:1646`, again at the caller's `:2105`) to end up with strictly less context.

This also matches the acceptance criterion that the fix add "no new branch and no invented
semantics".

### D2: The observable outcome comes from the existing caller handler, unchanged

`ImportError` is a subclass of `Exception` and is not a `ValueError`, so it passes the
`except ValueError` at `:2097` and lands in `except Exception` at `:2104` — which already logs,
emits the `warning`, and sets `override_llm = None`. Nothing at the call site needs to change.
This is verified by inspection of the clause order and must be re-confirmed by the end-to-end
test rather than assumed.

### D3: Two tests, because neither alone covers the defect

The seam that makes the caller testable is also what hides the bug:

- An **end-to-end test** substituting a `_create_provider_llm` that raises `ImportError` and
  driving `stream` proves the observable contract (a `warning` is emitted, the default pipeline
  answers). But it passes *before* the fix too — the substitute raises, bypassing the real
  clause. It is a regression guard, not a reproduction.
- A **direct test** of the real method is the one that fails before the fix. Force the lazy
  import at `:1623` to fail — setting `sys.modules["src.archi.providers"] = None` via
  `monkeypatch.setitem` makes `from src.archi.providers import get_provider` raise `ImportError`
  — then assert the method raises rather than returning `None`.

Both are required. Writing only the first would produce a green test suite and an unfixed bug.

The end-to-end test should follow the shape of
`test_override_generic_error_warns_and_falls_back_to_default`
(`tests/unit/test_chat_override_persistence.py:341`), reusing the `_make_stream_wrapper` /
`_drive_stream` helpers at `:339` and `:366`.

### D4: Update the docstring's return contract along with the code

The docstring at `:1616-1621` promises "A LangChain BaseChatModel instance, or None if creation
fails". After D1 no failure path returns `None`, so that sentence becomes false at the moment the
clause is deleted. It is part of the contract being changed, not incidental cleanup, and is
updated in the same commit.

### D5: Check the black seam before editing `app.py`

`app.py` is large and has a documented reflow-churn trap: an in-place edit can cause black to
reformat an unrelated region, which both pollutes the diff and can sink diff coverage below the
gate's 80%. The `black-seam-scout` agent is run against the edit region before touching the file,
per the issue's constraint. The edit here is a three-line deletion inside an existing
`try`/`except`, which is low-risk, but the check is cheap and the acceptance criteria require
`git diff` on `app.py` to show no unrelated reflow.

## Risks / Trade-offs

- **A previously-silent misconfiguration starts emitting warnings on every overridden request** →
  Intended: that is the fix. Worth noting in the PR body, since an operator with a broken
  provider install will see new `warning` events where they previously saw clean responses. No
  client contract breaks — `warning` is an already-defined event type on this stream.
- **Losing the "Providers module not available" log string** → Mitigated by D1's reasoning: the
  underlying `ImportError` text is preserved in both the generic log line and the client-facing
  warning message. Anyone grepping logs for the old string would need to update; this is an
  internal log message, not an interface.
- **Something other than the known caller starts depending on the `None` return** → Verified as
  exactly one caller at `:2094` at implementation time; re-confirm with `grep -n
  "_create_provider_llm(" src/interfaces/chat_app/app.py` before committing, and state the
  finding in the PR per the acceptance criteria.
- **Black reflow inflates the diff and sinks diff coverage** → D5's seam check before editing;
  inspect `git diff` for unrelated hunks before committing.
- **The direct test's `sys.modules` manipulation leaks into other tests** → Use `monkeypatch`,
  which restores `sys.modules` at teardown, rather than assigning directly.

## Migration Plan

None. No data model, API surface, dependency, or deployment change. The fix is behavioural within
a single request path and takes effect on the next deploy with no operator action; rollback is a
straight revert of the commit.

## Open Questions

None. The issue body fixes the scope, the fallback semantics, and the two paths that must not
change; every decision above is resolvable from the code as it stands.
