"""Tests for gunicorn options, application construction, and entry point."""

import ast
import os
import re
import sys
import tomllib
import types
from pathlib import Path

import pytest
import yaml
from jinja2 import ChainableUndefined, Environment, PackageLoader, select_autoescape

from src.bin import service_chat

# ---------------------------------------------------------------------------
# Stub fixture (design E8)
# ---------------------------------------------------------------------------


class _StubConfig:
    SETTINGS = {"bind", "workers", "worker_class", "threads", "preload_app", "timeout"}

    def __init__(self):
        self._values = {}

    def set(self, name, value):
        if name not in self.SETTINGS:
            raise AttributeError(f"No setting: {name!r}")
        self._values[name] = value


class _StubBaseApplication:
    """Mirrors gunicorn 26.2.0 BaseApplication init contract."""

    def __init__(self, usage=None, prog=None):
        self.cfg = None
        self.callable = None
        self._run_called = False
        self.load_default_config()
        self.load_config()

    def load_default_config(self):
        self.cfg = _StubConfig()

    def load_config(self):
        pass

    def wsgi(self):
        if self.callable is None:
            self.callable = self.load()
        return self.callable

    def run(self):
        self._run_called = True


@pytest.fixture()
def gunicorn_stub(monkeypatch):
    """Install stub gunicorn modules; monkeypatch restores originals on teardown."""
    mod_gunicorn = types.ModuleType("gunicorn")
    mod_gunicorn_app = types.ModuleType("gunicorn.app")
    mod_gunicorn_app_base = types.ModuleType("gunicorn.app.base")
    mod_gunicorn_app_base.BaseApplication = _StubBaseApplication

    monkeypatch.setitem(sys.modules, "gunicorn", mod_gunicorn)
    monkeypatch.setitem(sys.modules, "gunicorn.app", mod_gunicorn_app)
    monkeypatch.setitem(sys.modules, "gunicorn.app.base", mod_gunicorn_app_base)
    return mod_gunicorn_app_base


# ---------------------------------------------------------------------------
# build_gunicorn_options
# ---------------------------------------------------------------------------


def test_build_gunicorn_options_defaults():
    opts = service_chat.build_gunicorn_options({"host": "0.0.0.0", "port": 7861})
    assert opts == {
        "bind": "0.0.0.0:7861",
        "workers": 1,
        "worker_class": "gthread",
        "threads": 16,
        "preload_app": False,
    }


def test_build_gunicorn_options_wsgi_threads_none():
    opts = service_chat.build_gunicorn_options(
        {"host": "0.0.0.0", "port": 7861, "wsgi_threads": None}
    )
    assert opts["threads"] == 16


def test_build_gunicorn_options_wsgi_threads_explicit():
    opts = service_chat.build_gunicorn_options(
        {"host": "0.0.0.0", "port": 7861, "wsgi_threads": 8}
    )
    assert opts["threads"] == 8


def test_build_gunicorn_options_bind_custom_host():
    opts = service_chat.build_gunicorn_options({"host": "127.0.0.1", "port": 9000})
    assert opts["bind"] == "127.0.0.1:9000"


@pytest.mark.parametrize("bad", [0, -1, True, False, "16", 2.5])
def test_build_gunicorn_options_bad_wsgi_threads(bad):
    with pytest.raises(ValueError, match="services.chat_app.wsgi_threads"):
        service_chat.build_gunicorn_options(
            {"host": "0.0.0.0", "port": 7861, "wsgi_threads": bad}
        )


# ---------------------------------------------------------------------------
# make_gunicorn_application (with stub)
# ---------------------------------------------------------------------------


def test_make_gunicorn_application_no_factory_call(gunicorn_stub):
    calls = []

    def factory():
        calls.append(1)
        return object()

    opts = {
        "bind": "0.0.0.0:7861",
        "workers": 1,
        "worker_class": "gthread",
        "threads": 16,
        "preload_app": False,
    }
    service_chat.make_gunicorn_application(factory, opts)
    assert calls == [], "factory must not be called during construction"


def test_make_gunicorn_application_options_reach_cfg(gunicorn_stub):
    def factory():
        return object()

    opts = {
        "bind": "0.0.0.0:7861",
        "workers": 1,
        "worker_class": "gthread",
        "threads": 16,
        "preload_app": False,
    }
    app = service_chat.make_gunicorn_application(factory, opts)
    for key, value in opts.items():
        assert app.cfg._values[key] == value


def test_make_gunicorn_application_wsgi_calls_factory_once(gunicorn_stub):
    sentinel = object()
    calls = []

    def factory():
        calls.append(1)
        return sentinel

    opts = {
        "bind": "0.0.0.0:7861",
        "workers": 1,
        "worker_class": "gthread",
        "threads": 16,
        "preload_app": False,
    }
    app = service_chat.make_gunicorn_application(factory, opts)
    result1 = app.wsgi()
    result2 = app.wsgi()
    assert result1 is sentinel
    assert result2 is sentinel
    assert (
        len(calls) == 1
    ), "factory must be called exactly once even if wsgi() called twice"


def test_make_gunicorn_application_unknown_option_raises(gunicorn_stub):
    def factory():
        return object()

    opts = {
        "bind": "0.0.0.0:7861",
        "workers": 1,
        "worker_class": "gthread",
        "threads": 16,
        "preload_app": False,
        "not_a_setting": "boom",
    }
    with pytest.raises(AttributeError):
        service_chat.make_gunicorn_application(factory, opts)


# ---------------------------------------------------------------------------
# AST test: no module-level gunicorn import
# ---------------------------------------------------------------------------


def test_no_module_level_gunicorn_import():
    source = Path(__file__).parent.parent.parent / "src" / "bin" / "service_chat.py"
    tree = ast.parse(source.read_text())
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(
                    "gunicorn"
                ), f"module-level 'import {alias.name}' found in service_chat.py"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith(
                "gunicorn"
            ), f"module-level 'from {module} import ...' found in service_chat.py"


# ---------------------------------------------------------------------------
# Real-gunicorn test (skipped in gate env; runs in PR Preview unit-tests job)
# ---------------------------------------------------------------------------


def test_make_gunicorn_application_real_gunicorn():
    pytest.importorskip("gunicorn")
    factory_calls = []
    sentinel = object()

    def factory():
        factory_calls.append(1)
        return sentinel

    options = service_chat.build_gunicorn_options({"host": "0.0.0.0", "port": 7861})
    app = service_chat.make_gunicorn_application(factory, options)

    assert factory_calls == [], "factory must not be called during construction"
    assert app.cfg.bind == ["0.0.0.0:7861"]
    assert app.cfg.workers == 1
    assert app.cfg.worker_class_str == "gthread"
    assert app.cfg.threads == 16
    assert app.cfg.preload_app is False

    result = app.wsgi()
    assert result is sentinel
    assert len(factory_calls) == 1


# ---------------------------------------------------------------------------
# Helpers for create_app / main tests
# ---------------------------------------------------------------------------


def _make_fake_factory():
    """Return (FakeFactoryClass, log_dict).

    log['from_env'] — FakeFactory instances returned by from_env.
    log['set_instance'] — each value passed to set_instance.
    Each instance has ._closed set to True by close().
    """
    log = {"from_env": [], "set_instance": []}

    class FakeFactory:
        def __init__(self):
            self._closed = False

        @classmethod
        def from_env(cls, **kwargs):
            inst = cls()
            log["from_env"].append(inst)
            return inst

        @classmethod
        def set_instance(cls, value):
            log["set_instance"].append(value)

        def close(self):
            self._closed = True

    return FakeFactory, log


def _make_chat_config(tmp_path, extra=None):
    template_dir = tmp_path / "templates"
    static_dir = tmp_path / "static"
    template_dir.mkdir(exist_ok=True)
    static_dir.mkdir(exist_ok=True)
    cfg = {
        "host": "0.0.0.0",
        "port": 7861,
        "hostname": "chat.example.com",
        "external_port": 443,
        "template_folder": str(template_dir),
        "static_folder": str(static_dir),
    }
    if extra:
        cfg.update(extra)
    return cfg


# ---------------------------------------------------------------------------
# create_app
# ---------------------------------------------------------------------------


def test_create_app_returns_flask_debug_false(monkeypatch, tmp_path):
    import flask

    FakeFactory, log = _make_fake_factory()
    chat_config = {
        "template_folder": str(tmp_path / "templates"),
        "static_folder": str(tmp_path / "static"),
    }
    (tmp_path / "templates").mkdir()
    (tmp_path / "static").mkdir()

    generate_calls = []
    instrument_calls = []
    wrapper_calls = []

    monkeypatch.setattr(service_chat, "PostgresServiceFactory", FakeFactory)
    monkeypatch.setattr(
        service_chat,
        "get_full_config",
        lambda: {"services": {"chat_app": chat_config}},
    )
    monkeypatch.setattr(service_chat, "read_secret", lambda k: f"secret-{k}")
    monkeypatch.setattr(
        service_chat, "generate_script", lambda cfg: generate_calls.append(cfg)
    )
    monkeypatch.setattr(
        service_chat,
        "instrument_flask_app",
        lambda app: instrument_calls.append(app),
    )
    monkeypatch.setattr(
        service_chat, "FlaskAppWrapper", lambda app: wrapper_calls.append(app)
    )
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    result = service_chat.create_app()

    assert isinstance(result, flask.Flask)
    assert result.debug is False
    assert len(log["from_env"]) == 1
    assert log["set_instance"] == [log["from_env"][0]]
    assert generate_calls == [chat_config]
    assert len(instrument_calls) == 1
    assert instrument_calls[0] is result
    assert len(wrapper_calls) == 1
    assert wrapper_calls[0] is result
    assert os.environ.get("ANTHROPIC_API_KEY") == "secret-ANTHROPIC_API_KEY"
    assert os.environ.get("OPENAI_API_KEY") == "secret-OPENAI_API_KEY"
    assert os.environ.get("HUGGING_FACE_HUB_TOKEN") == "secret-HUGGING_FACE_HUB_TOKEN"


def test_create_app_debug_false_with_flask_debug_env(monkeypatch, tmp_path):
    """FLASK_DEBUG=1 in environment does not make app.debug True."""
    FakeFactory, _ = _make_fake_factory()
    (tmp_path / "templates").mkdir()
    (tmp_path / "static").mkdir()
    chat_config = {
        "template_folder": str(tmp_path / "templates"),
        "static_folder": str(tmp_path / "static"),
    }

    monkeypatch.setattr(service_chat, "PostgresServiceFactory", FakeFactory)
    monkeypatch.setattr(
        service_chat,
        "get_full_config",
        lambda: {"services": {"chat_app": chat_config}},
    )
    monkeypatch.setattr(service_chat, "read_secret", lambda k: f"secret-{k}")
    monkeypatch.setattr(service_chat, "generate_script", lambda cfg: None)
    monkeypatch.setattr(service_chat, "instrument_flask_app", lambda app: None)
    monkeypatch.setattr(service_chat, "FlaskAppWrapper", lambda app: None)
    monkeypatch.setenv("FLASK_DEBUG", "1")

    result = service_chat.create_app()
    assert result.debug is False


def test_create_app_debug_false_with_flask_debug_mode_config(monkeypatch, tmp_path):
    """flask_debug_mode: True in config does not make app.debug True."""
    FakeFactory, _ = _make_fake_factory()
    (tmp_path / "templates").mkdir()
    (tmp_path / "static").mkdir()
    chat_config = {
        "template_folder": str(tmp_path / "templates"),
        "static_folder": str(tmp_path / "static"),
        "flask_debug_mode": True,
    }

    monkeypatch.setattr(service_chat, "PostgresServiceFactory", FakeFactory)
    monkeypatch.setattr(
        service_chat,
        "get_full_config",
        lambda: {"services": {"chat_app": chat_config}},
    )
    monkeypatch.setattr(service_chat, "read_secret", lambda k: f"secret-{k}")
    monkeypatch.setattr(service_chat, "generate_script", lambda cfg: None)
    monkeypatch.setattr(service_chat, "instrument_flask_app", lambda app: None)
    monkeypatch.setattr(service_chat, "FlaskAppWrapper", lambda app: None)

    result = service_chat.create_app()
    assert result.debug is False


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_run_called_factory_closed_set_instance_none(
    gunicorn_stub, monkeypatch, tmp_path
):
    """main() closes factory and clears singleton before server.run(); run() called once."""
    FakeFactory, log = _make_fake_factory()
    chat_config = _make_chat_config(tmp_path)

    create_app_calls = []
    monkeypatch.setattr(
        service_chat, "create_app", lambda: create_app_calls.append(1) or object()
    )
    monkeypatch.setattr(service_chat, "PostgresServiceFactory", FakeFactory)
    monkeypatch.setattr(
        service_chat,
        "get_full_config",
        lambda: {"services": {"chat_app": chat_config}},
    )
    monkeypatch.setattr(service_chat, "read_secret", lambda k: f"secret-{k}")
    monkeypatch.setattr(service_chat, "setup_logging", lambda: None)

    run_count = [0]
    run_states = {}

    def _capturing_run(self):
        run_count[0] += 1
        run_states["factory_closed"] = log["from_env"][0]._closed
        run_states["last_set_instance"] = log["set_instance"][-1]
        run_states["app_factory"] = self.app_factory
        run_states["cfg_values"] = dict(self.cfg._values)
        self._run_called = True

    monkeypatch.setattr(_StubBaseApplication, "run", _capturing_run)

    service_chat.main()

    assert run_count[0] == 1
    assert run_states["factory_closed"] is True
    assert run_states["last_set_instance"] is None
    assert run_states["app_factory"] is service_chat.create_app
    assert create_app_calls == []
    expected_opts = service_chat.build_gunicorn_options(chat_config)
    assert run_states["cfg_values"] == expected_opts


def test_main_get_full_config_raises_closes_factory(gunicorn_stub, monkeypatch):
    """get_full_config raising RuntimeError closes factory and does not call run()."""
    FakeFactory, log = _make_fake_factory()

    def _raise():
        raise RuntimeError("config unavailable")

    run_called = [False]

    def _run(self):
        run_called[0] = True

    monkeypatch.setattr(_StubBaseApplication, "run", _run)
    monkeypatch.setattr(service_chat, "PostgresServiceFactory", FakeFactory)
    monkeypatch.setattr(service_chat, "get_full_config", _raise)
    monkeypatch.setattr(service_chat, "read_secret", lambda k: f"secret-{k}")
    monkeypatch.setattr(service_chat, "setup_logging", lambda: None)

    with pytest.raises(RuntimeError):
        service_chat.main()

    assert log["from_env"][0]._closed is True
    assert not run_called[0]


def test_main_bad_wsgi_threads_raises_no_run(gunicorn_stub, monkeypatch, tmp_path):
    """wsgi_threads: 0 causes ValueError before run() is called."""
    FakeFactory, _ = _make_fake_factory()
    chat_config = _make_chat_config(tmp_path, extra={"wsgi_threads": 0})

    run_called = [False]

    def _run(self):
        run_called[0] = True

    monkeypatch.setattr(_StubBaseApplication, "run", _run)
    monkeypatch.setattr(service_chat, "PostgresServiceFactory", FakeFactory)
    monkeypatch.setattr(
        service_chat,
        "get_full_config",
        lambda: {"services": {"chat_app": chat_config}},
    )
    monkeypatch.setattr(service_chat, "read_secret", lambda k: f"secret-{k}")
    monkeypatch.setattr(service_chat, "setup_logging", lambda: None)

    with pytest.raises(ValueError):
        service_chat.main()

    assert not run_called[0]


# ---------------------------------------------------------------------------
# Source-level check
# ---------------------------------------------------------------------------


def test_no_debug_or_app_run_in_service_chat():
    source = Path(__file__).parent.parent.parent / "src" / "bin" / "service_chat.py"
    assert re.search(r"debug=True|app\.run\(", source.read_text()) is None


# ---------------------------------------------------------------------------
# Dependency pin
# ---------------------------------------------------------------------------


def test_gunicorn_pin_in_pyproject_toml():
    from packaging.requirements import Requirement

    pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
    with open(pyproject, "rb") as fh:
        data = tomllib.load(fh)

    deps = data["project"]["dependencies"]
    gunicorn_entries = [d for d in deps if Requirement(d).name == "gunicorn"]
    assert (
        len(gunicorn_entries) == 1
    ), f"Expected one gunicorn dep, got {gunicorn_entries}"
    spec = str(Requirement(gunicorn_entries[0]).specifier)
    assert spec == "==26.2.0", f"Expected ==26.2.0, got {spec!r}"

    req_base = (
        Path(__file__).parent.parent.parent / "requirements" / "requirements-base.txt"
    )
    for line in req_base.read_text().splitlines():
        pkg = line.split("#")[0].strip()
        assert (
            "gunicorn" not in pkg.lower()
        ), f"gunicorn in requirements-base.txt: {line!r}"


# ---------------------------------------------------------------------------
# Config template render tests (design E6)
# ---------------------------------------------------------------------------


def _render_base_config(**kwargs):
    env = Environment(
        loader=PackageLoader("src.cli"),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    template = env.get_template("base-config.yaml")
    rendered = template.render(verbosity=0, **kwargs)
    return yaml.safe_load(rendered)


def test_wsgi_threads_default_is_16():
    cfg = _render_base_config()
    assert cfg["services"]["chat_app"]["wsgi_threads"] == 16


def test_flask_debug_mode_default_is_false():
    cfg = _render_base_config()
    assert cfg["services"]["chat_app"]["flask_debug_mode"] is False


def test_wsgi_threads_explicit_8():
    cfg = _render_base_config(services={"chat_app": {"wsgi_threads": 8}})
    assert cfg["services"]["chat_app"]["wsgi_threads"] == 8


def test_wsgi_threads_none_gives_16():
    cfg = _render_base_config(services={"chat_app": {"wsgi_threads": None}})
    assert cfg["services"]["chat_app"]["wsgi_threads"] == 16


def test_flask_debug_mode_true_renders_true():
    cfg = _render_base_config(services={"chat_app": {"flask_debug_mode": True}})
    assert cfg["services"]["chat_app"]["flask_debug_mode"] is True


def test_flask_debug_mode_none_renders_false():
    cfg = _render_base_config(services={"chat_app": {"flask_debug_mode": None}})
    assert cfg["services"]["chat_app"]["flask_debug_mode"] is False


# ---------------------------------------------------------------------------
# Configuration reference (design E9)
# ---------------------------------------------------------------------------

_CONFIGURATION_DOC = (
    Path(__file__).resolve().parents[2] / "docs" / "docs" / "configuration.md"
)


def _doc_section(heading):
    """The text under heading, up to the next heading of any depth."""
    text = _CONFIGURATION_DOC.read_text(encoding="utf-8")
    assert heading in text, f"configuration.md has no {heading!r} section"
    rest = text[text.index(heading) + len(heading) :]
    nxt = re.search(r"^#{1,6} ", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def _doc_table_row(section, key):
    """The cells of the table row whose first cell is `key`, or None."""
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] == f"`{key}`":
            return cells
    return None


def test_chat_app_doc_row_for_wsgi_threads_matches_code_default():
    row = _doc_table_row(_doc_section("### `services.chat_app`"), "wsgi_threads")
    assert row is not None, "services.chat_app table has no wsgi_threads row"
    threads = service_chat.build_gunicorn_options({"host": "h", "port": 1})["threads"]
    rendered = _render_base_config()["services"]["chat_app"]["wsgi_threads"]
    assert row[2] == f"`{threads}`" == f"`{rendered}`", row


def test_chat_app_doc_row_for_flask_debug_mode_matches_template_default():
    row = _doc_table_row(_doc_section("### `services.chat_app`"), "flask_debug_mode")
    assert row is not None, "services.chat_app table has no flask_debug_mode row"
    rendered = _render_base_config()["services"]["chat_app"]["flask_debug_mode"]
    assert row[2] == f"`{str(rendered).lower()}`", row


def test_chat_app_doc_names_the_gunicorn_worker_model():
    section = _doc_section("### `services.chat_app`")
    options = service_chat.build_gunicorn_options({"host": "h", "port": 1})
    assert "gunicorn" in section
    assert f"`{options['worker_class']}`" in section
    assert options["workers"] == 1 and "one worker process" in section


def test_chat_flask_debug_mode_listed_as_not_acted_on():
    section = _doc_section("### Flags that render but are not yet acted on")
    row = _doc_table_row(section, "services.chat_app.flask_debug_mode")
    assert row is not None, "chat flask_debug_mode missing from the not-acted-on table"
