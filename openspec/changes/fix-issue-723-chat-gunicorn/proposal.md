## Why

The chat service runs on Flask's development server with the Werkzeug debugger on (issue #723, non-negotiable 2 of #81, milestone `v2026.11.0`). On `origin/dev` `82850b3b`:

- `src/bin/service_chat.py:48-53`: `main()` calls `app.run(debug=True, use_reloader=False, port=..., host=...)`. `FlaskAppWrapper.run` (`src/interfaces/chat_app/app.py:3850`) only calls `self.app.run(**kwargs)`. The Werkzeug interactive debugger can run code from a browser when an exception occurs.
- `src/cli/templates/base-config.yaml:217-218`: `services.chat_app.flask_debug_mode` renders `true` by default, but no code in the chat path reads it.
- No production WSGI server is in `pyproject.toml`, `requirements/`, or `src/`.

The operator decided the server and process model on 2026-10-19: gunicorn, 1 worker process, worker class `gthread`, a thread count from a new key `services.chat_app.wsgi_threads` (default 16), no preload. That decision is closed.

## What Changes

- `src/bin/service_chat.py`:
  - `build_gunicorn_options(chat_config)` returns `bind`, `workers=1`, `worker_class="gthread"`, `threads`, and `preload_app=False`. It refuses a `wsgi_threads` value that is not a positive integer.
  - `make_gunicorn_application(app_factory, options)` imports `gunicorn.app.base` when it is called (not at module import) and returns a `BaseApplication` subclass instance. Its `load()` calls the app factory, so the Flask app is built in the worker, after the fork.
  - `create_app()` reads the secrets, builds its own `PostgresServiceFactory`, reads the config, writes `script.js`, builds the Flask app, instruments it, registers the routes with `FlaskAppWrapper`, forces `debug` off, and returns the Flask app.
  - `main()` sets up logging, reads the chat config once, closes the Postgres pool that the read opened, and runs the gunicorn application. No `app.run(` call and no `debug=True` text stays in the file.
- `src/cli/templates/base-config.yaml`: a new `services.chat_app.wsgi_threads` key (default 16, null-safe ternary form), and the `services.chat_app.flask_debug_mode` default changes from `true` to `false`.
- `pyproject.toml`: one pin, `gunicorn==26.2.0`, in `[project].dependencies` only. `requirements/requirements-base.txt` does not change.
- `docs/docs/configuration.md`: document `wsgi_threads`, the gunicorn model, and that `flask_debug_mode` has no effect on the chat service.

Out of scope (issue #723): more than one worker process, a move of `EvaluationJobManager` state out of memory, a reverse proxy or TLS, the data manager and grader development servers, any Dockerfile `CMD` change, auth, and the archi-config deployment config.

## Capabilities

### New Capabilities

- `chat-app-serving`: how the chat service process serves the Flask app (server, process model, debugger, config keys).

### Modified Capabilities

None.

## Impact

- Code: `src/bin/service_chat.py`, `src/cli/templates/base-config.yaml`, `pyproject.toml`.
- Tests: new `tests/unit/test_service_chat_wsgi.py`; one row in `tests/unit/test_base_config_falsy_defaults_render.py` (`services.chat_app.flask_debug_mode` default `True` → `False`). `tests/unit/test_telemetry.py` does not change.
- Docs: `docs/docs/configuration.md` (`services.chat_app`).
- Images: `Dockerfile-chat:41` and `Dockerfile-chat-gpu:74` run `pip install .`, so the chat images get gunicorn with no base-image rebuild. The `CMD` does not change.
- Runtime: the chat container's PID 1 becomes the gunicorn arbiter. Werkzeug's per-request access lines stop (gunicorn's access log is off by default).
