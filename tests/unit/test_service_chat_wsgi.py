"""Tests for gunicorn options, application construction, and entry point."""

import ast
import sys
import types
from pathlib import Path

import pytest

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
