## Context

Issue #723 is a work order with a closed operator decision (2026-10-19): gunicorn, 1 worker process, worker class `gthread`, threads from `services.chat_app.wsgi_threads` (default 16), no preload. This design does not reopen it. It records how to build it, and three facts from the tree that the issue body does not state (D1, D2, D3).

Anchors on `origin/dev` `82850b3b`:

- `src/bin/service_chat.py:14-53` `main()`; `:56-73` `generate_script()`; `:76-78` the `__main__` block (`mp.set_start_method("spawn", force=True)`, then `main()`). The `__main__` block does not change.
- `src/utils/postgres_service_factory.py:132` `from_env()`, `:176` `set_instance()`, `:227-237` `close()` (closes the pool and clears the service references).
- `src/utils/config_access.py:73` `get_full_config()` reads through `PostgresServiceFactory.get_instance().config_service`. The first read opens a `psycopg2` `ThreadedConnectionPool` (`src/utils/connection_pool.py:96`).
- `src/utils/logging.py:25-53` `setup_logging()` calls `telemetry.init_telemetry()`, which can attach a `BatchSpanProcessor` (`src/utils/telemetry.py:435-452`). `init_telemetry()` is idempotent through the module global `_STATUS`.
- `src/interfaces/chat_app/app.py:2779` `FlaskAppWrapper.__init__` reads the config and the secrets, and registers the routes on the Flask app.

## Facts the issue body does not state

### D1. gunicorn is not installed where the unit tests run

`.github/workflows/ci.yml:47-51` (the `gate` check) installs `requirements/requirements-base.txt` only. The issue forbids a gunicorn line in that file. The host test env (`miniforge3/envs/archi`) and the loop container also have no gunicorn (`import gunicorn` → `ModuleNotFoundError`, checked 2026-10-20). As a result:

- `src/bin/service_chat.py` MUST NOT import gunicorn at module level. Only `make_gunicorn_application()` imports it, when it is called.
- The unit tests stub `gunicorn`, `gunicorn.app`, and `gunicorn.app.base` in `sys.modules` (E8). Do NOT install gunicorn into the gate env to make a test pass.
- One extra test uses `pytest.importorskip("gunicorn")` and builds the REAL application. The gate skips it. The PR Preview `unit-tests` job runs it, because that job runs `pip install .` (`.github/workflows/pr-preview.yml:46-54`).
- The PR Preview `preview` job deploys the chat app (`services: chatbot`) when `src/` or `pyproject.toml` changes (`pr-preview.yml:200`, `:304-321`). That job is the live check that gunicorn serves the app.

### D2. `default(16, true)` fails a frozen guard

`tests/unit/test_base_config_falsy_defaults_render.py:480-484` freezes `_NON_BOOL_DEFAULT_BASELINE`: "The form may leave this list; nothing may join it." `test_guard_default_filter_baseline_is_unchanged` (`:634`) fails on a new `| default(<literal>, true)` call site. The issue's step 3 text (`default(16, true)`) therefore fails the gate. Use the null-safe ternary form that `flask_debug_mode` already uses (E6). Do NOT add an entry to the baseline.

### D3. The `flask_debug_mode` default is pinned by an existing test

`tests/unit/test_base_config_falsy_defaults_render.py:206-211` (`_NULL_ROWS`, starts at `:191`) has the row `("services.chat_app.flask_debug_mode", "services.chat_app.flask_debug_mode", True, {})`. `test_absent_renders_default` and `test_none_renders_default` fail when the template default changes to `false`. Change that row's default to `False` in the same commit as the template. Do not change the `services.grader_app.flask_debug_mode` row.

## Goals / Non-Goals

**Goals:** the chat service runs on gunicorn as the operator decided; the Werkzeug debugger can never run in the chat service; the parent process opens no Postgres connection that a worker uses; the change is fully unit-tested without gunicorn installed.

**Non-Goals:** more than one worker; IPv6 `host` values (the bind string is `f"{host}:{port}"`, as the issue states); create-time validation of `wsgi_threads` in `src/cli/managers/config_manager.py`; a gunicorn access log; any change to `FlaskAppWrapper.run` (it stays, unused by the chat path); the data manager and grader servers.

## Decisions

### E1. `build_gunicorn_options(chat_config) -> dict`

Returns exactly these five keys:

```python
{
    "bind": f"{chat_config['host']}:{chat_config['port']}",
    "workers": 1,
    "worker_class": "gthread",
    "threads": <threads>,
    "preload_app": False,
}
```

`threads` comes from `chat_config.get("wsgi_threads")`. Absent or `None` → `16`. Any other value MUST be an `int` that is not a `bool` and is at least 1; else raise `ValueError` with a message that contains `services.chat_app.wsgi_threads`. Reasons: gunicorn's own validator (`validate_pos_int`, gunicorn 26.2.0 `config.py:398`) accepts `0` and booleans, and a gthread worker with 0 threads cannot serve. A `"16"` string, `2.5`, `0`, `-1`, `True`, and `False` are all refused.

### E2. `make_gunicorn_application(app_factory, options)`

```python
def make_gunicorn_application(app_factory, options):
    from gunicorn.app.base import BaseApplication

    class ChatApplication(BaseApplication):
        def __init__(self):
            self.app_factory = app_factory
            self.options = dict(options)
            super().__init__()

        def load_config(self):
            for key, value in self.options.items():
                self.cfg.set(key, value)

        def load(self):
            return self.app_factory()

    return ChatApplication()
```

- Set `self.options` BEFORE `super().__init__()`: gunicorn 26.2.0 `BaseApplication.__init__` calls `do_load_config()` → `load_default_config()` then `load_config()` (`gunicorn/app/base.py:21-39`).
- `load_config()` sets every option with no filter. An unknown name raises (`Config.set` raises `AttributeError`, `config.py:81-84`), so a typo cannot pass silently.
- `load()` is called by `wsgi()` in the worker after the fork (`preload_app` is `False`). Constructing the object MUST NOT call `app_factory`.
- `BaseApplication` (not `Application`) does not read `GUNICORN_CMD_ARGS`, a `gunicorn.conf.py`, or `sys.argv`. Keep it that way.

### E3. `create_app()`

In this order, the same statements as today's `main()` from the secrets onward:

1. Set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `HUGGING_FACE_HUB_TOKEN` in `os.environ` from `read_secret`.
2. `factory = PostgresServiceFactory.from_env(password_override=read_secret("PG_PASSWORD"))`; `PostgresServiceFactory.set_instance(factory)`. This is the worker's own pool.
3. `chat_config = get_full_config()["services"]["chat_app"]`; `generate_script(chat_config)`.
4. `flask_app = Flask(__name__, template_folder=..., static_folder=...)`; `instrument_flask_app(flask_app)`; `FlaskAppWrapper(flask_app)`.
5. `flask_app.debug = False`, then `return flask_app`.

Step 5 is explicit because Flask 3.0 sets `DEBUG` from the `FLASK_DEBUG` environment variable when the app is built. `create_app()` never reads `flask_debug_mode`. It does not call `setup_logging()` (the worker inherits the parent's logging, and `init_telemetry()` is idempotent).

### E4. `main()`

```python
def main():
    setup_logging()
    factory = PostgresServiceFactory.from_env(
        password_override=read_secret("PG_PASSWORD")
    )
    PostgresServiceFactory.set_instance(factory)
    try:
        chat_config = get_full_config()["services"]["chat_app"]
    finally:
        factory.close()
        PostgresServiceFactory.set_instance(None)
    print(...)  # the two existing "Starting Chat Service" / "Accessible externally" lines
    options = build_gunicorn_options(chat_config)
    server = make_gunicorn_application(create_app, options)
    server.run()
```

- The parent closes its pool and clears the singleton BEFORE the arbiter starts, so no connection the parent opened can reach a worker. The `try`/`finally` closes it even when the read raises.
- `build_gunicorn_options()` runs before the arbiter starts, so a bad `wsgi_threads` stops the process with a plain traceback.
- Name the variable `server` (or any name that does not end in `app`). The acceptance check is `git grep -n "debug=True\|app.run(" -- src/bin/service_chat.py`, and in that pattern `.` matches any character, so `gunicorn_app.run(` matches it. The text `debug=True` MUST NOT appear anywhere in the file, comments and docstrings included.

### E5. Telemetry across the fork (no change needed)

`setup_logging()` in the parent can attach a `BatchSpanProcessor`. opentelemetry-sdk 1.44.0 (the pinned version) re-creates the export thread in a forked child: `opentelemetry/sdk/_shared_internal/__init__.py:117-119` registers `os.register_at_fork(after_in_child=...)`. The worker's `instrument_flask_app()` reuses the inherited status. The service name stays `archi-chat` (`resolve_service_name` reads `sys.argv[0]`, which does not change). `tests/unit/test_telemetry.py` does not change.

### E6. Template (`src/cli/templates/base-config.yaml`)

Insert right after the `host:` line (`:211`), in the same form as `:217-218`:

```
    {%- set v_chat_wsgi_threads = services.chat_app.wsgi_threads %}
    wsgi_threads: {{ v_chat_wsgi_threads if v_chat_wsgi_threads is defined and v_chat_wsgi_threads is not none else 16 }}
```

Change `:218` from `... else true }}` to `... else false }}`. An explicit operator value still wins: `flask_debug_mode: true` renders `true` (the chat service ignores it, E3). See D2 and D3 for the two test consequences.

### E7. Dependency pin

Append `"gunicorn==26.2.0"` to `pyproject.toml` `[project].dependencies`, with a short comment above it in the style of the neighbors (issue #723: the chat service's WSGI server; not in requirements-base.txt, so the chat image gets it from `pip install .`). The current last entry `"flask-cors==5.0.0"` has no trailing comma; add one. gunicorn 26.2.0 was uploaded 2026-08-24 (57 days before 2026-10-20), has no required runtime dependencies, and needs Python >= 3.10. Do NOT edit `requirements/requirements-base.txt`.

### E8. The gunicorn stub for unit tests

A `pytest` fixture in `tests/unit/test_service_chat_wsgi.py` builds three `types.ModuleType` objects and installs them with `monkeypatch.setitem(sys.modules, ...)` for `"gunicorn"`, `"gunicorn.app"`, and `"gunicorn.app.base"`. The stub `BaseApplication` mirrors gunicorn 26.2.0:

- `__init__(self, usage=None, prog=None)`: `self.cfg = None`, `self.callable = None`, then `self.load_default_config()`, then `self.load_config()`.
- `load_default_config()`: `self.cfg` = a stub config with a `settings` dict (keys at least `bind`, `workers`, `worker_class`, `threads`, `preload_app`, `timeout`) and `set(name, value)` that raises `AttributeError` for a name not in `settings`.
- `wsgi()`: `if self.callable is None: self.callable = self.load()`; return it.
- `run()`: records that it was called (the test reads the record).

The fixture works whether or not the real gunicorn is installed (the stub replaces it for the test, and `monkeypatch` restores it).

The real-gunicorn test (`pytest.importorskip("gunicorn")`, no stub) builds `make_gunicorn_application(factory, build_gunicorn_options({"host": "0.0.0.0", "port": 7861}))` and asserts: `factory` not called; `cfg.bind == ["0.0.0.0:7861"]`; `cfg.workers == 1`; `cfg.worker_class_str == "gthread"`; `cfg.threads == 16`; `cfg.preload_app is False`; `wsgi()` returns the factory's object and calls the factory once. Do NOT call `run()`. Do NOT read `cfg.worker_class` (that property imports the worker class).

### E9. Docs (`docs/docs/configuration.md`, `### services.chat_app`, the table at `:274-289`)

Add a `wsgi_threads` row after `host`: int, default `16`, "Threads in the one gunicorn worker process. Each open chat stream holds one thread. Must be 1 or more." Under the table, add a short paragraph: the chat service runs on gunicorn with one worker process and the `gthread` worker; it never runs the Flask debugger; `flask_debug_mode` (default `false`) has no effect on the chat service.

## Test oracle (issue #723 acceptance criteria)

| Acceptance criterion | Proof |
|---|---|
| `git grep -n "debug=True\|app.run(" -- src/bin/service_chat.py` prints nothing | the command, plus a unit test that reads the file and applies the same regex |
| New tests fail on `origin/dev`, pass on the branch; `test_telemetry.py` unchanged | red runs recorded in commit bodies; `git diff origin/dev -- tests/unit/test_telemetry.py` is empty |
| Exactly one `gunicorn==` pin in `pyproject.toml`; `requirements-base.txt` unchanged | a unit test that parses `[project].dependencies`; `git diff origin/dev -- requirements/` is empty |
| Rendered `wsgi_threads: 16` and `flask_debug_mode: false` by default | render tests (E6) |
| Gate green; CI green including `preview` | `bash scripts/gate.sh`; PR checks (after the PR opens) |
| PR targets `fasrc/archi:dev`; no `Co-Authored-By` | the PR task |

## Risks / Trade-offs

- **Concurrency limit.** One process with 16 threads serves at most 16 requests at the same time. A 17th request (for example a 17th open stream) waits for a free thread. `wsgi_threads` raises the limit.
- **No access log.** The Werkzeug server wrote one line per request. gunicorn's access log is off by default, and this change does not turn it on (the operator's option list does not include it).
- **Shutdown.** On `SIGTERM` the arbiter gives the worker `graceful_timeout` (30 s) and then kills it, so a stream that is open longer than that ends.
- **Worker boot failure.** If `create_app()` raises in the worker (for example Postgres is not ready), gunicorn stops the arbiter with a boot error and the container exits, as `main()` does today.
- **Real gunicorn is not in the gate.** The stub (E8) tests the code's contract with gunicorn. The real API is checked by the importorskip test in the PR Preview `unit-tests` job and by the `preview` smoke deployment.
