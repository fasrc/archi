## ADDED Requirements

### Requirement: The chat service SHALL serve the Flask app with gunicorn in one gthread worker process
The chat service entry point (`src/bin/service_chat.py`) MUST serve the chat Flask app with gunicorn, with exactly one worker process, worker class `gthread`, a thread count from `services.chat_app.wsgi_threads`, a bind address of `<host>:<port>` from `services.chat_app`, and no preload. It MUST NOT start Flask's or Werkzeug's development server.

#### Scenario: Default options
- **WHEN** the chat config has `host` `"0.0.0.0"`, `port` `7861`, and no `wsgi_threads` key
- **THEN** the gunicorn options are `bind` `"0.0.0.0:7861"`, `workers` `1`, `worker_class` `"gthread"`, `threads` `16`, and `preload_app` `False`

#### Scenario: A configured thread count
- **WHEN** the chat config has `wsgi_threads` `8`
- **THEN** the gunicorn option `threads` is `8`

#### Scenario: The entry point runs gunicorn
- **WHEN** `main()` runs with a valid chat config
- **THEN** it runs the gunicorn application once, and `src/bin/service_chat.py` contains no match for the pattern `debug=True\|app.run(`

### Requirement: The chat service SHALL never run the Werkzeug debugger
The app that the chat service serves MUST have `debug` set to `False`, whatever the value of the `FLASK_DEBUG` environment variable or of `services.chat_app.flask_debug_mode`.

#### Scenario: FLASK_DEBUG is set
- **WHEN** `create_app()` runs with the environment variable `FLASK_DEBUG=1`
- **THEN** the returned Flask app has `debug` `False`

#### Scenario: The config asks for debug mode
- **WHEN** `create_app()` runs with `services.chat_app.flask_debug_mode` `true`
- **THEN** the returned Flask app has `debug` `False`

### Requirement: Each worker SHALL build the Flask app after the fork
The gunicorn application object MUST NOT build the Flask app when it is created. Its `load()` MUST build the app by calling the app factory once, so that each worker builds the app, its Postgres pool, and its in-memory state in its own process.

#### Scenario: Creating the application object
- **WHEN** the gunicorn application object is created with an app factory
- **THEN** the app factory has not been called

#### Scenario: The worker loads the app
- **WHEN** gunicorn asks the application object for the WSGI app
- **THEN** the app factory is called exactly once and its return value is the WSGI app

### Requirement: The parent process SHALL close its Postgres pool before gunicorn starts
`main()` MUST close the Postgres connection pool that it opened to read the chat config, and MUST clear the `PostgresServiceFactory` singleton, before it runs the gunicorn application. It MUST do this also when the config read raises.

#### Scenario: Normal start
- **WHEN** `main()` reads the chat config and then runs gunicorn
- **THEN** the factory that `main()` built is closed, and the singleton is `None`, before the gunicorn application runs

#### Scenario: The config read fails
- **WHEN** the config read in `main()` raises
- **THEN** the factory that `main()` built is closed and the exception propagates

### Requirement: wsgi_threads SHALL be a positive integer that defaults to 16
The rendered config MUST contain `services.chat_app.wsgi_threads`, `16` by default and when the operator sets `null`. The chat service MUST refuse, with a `ValueError` that names `services.chat_app.wsgi_threads`, a value that is not an integer, is a boolean, or is less than 1.

#### Scenario: Rendered default
- **WHEN** `base-config.yaml` is rendered with no `wsgi_threads` value, or with `null`
- **THEN** the rendered `services.chat_app.wsgi_threads` is `16`

#### Scenario: Invalid values
- **WHEN** the chat config has `wsgi_threads` `0`, `-1`, `True`, `False`, `"16"`, or `2.5`
- **THEN** building the gunicorn options raises `ValueError` with a message that contains `services.chat_app.wsgi_threads`

### Requirement: The rendered chat flask_debug_mode SHALL default to false
`base-config.yaml` MUST render `services.chat_app.flask_debug_mode` as `false` when the operator does not set it or sets `null`, and MUST keep an explicit operator value.

#### Scenario: Not set
- **WHEN** `base-config.yaml` is rendered with no `services.chat_app.flask_debug_mode` value
- **THEN** the rendered value is `false`

#### Scenario: Explicit true
- **WHEN** `base-config.yaml` is rendered with `services.chat_app.flask_debug_mode` `true`
- **THEN** the rendered value is `true`
