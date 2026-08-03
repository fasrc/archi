"""Unauthenticated requests get an answer their caller can act on (issue #176).

``FlaskAppWrapper.require_auth`` (``app.py:3433``) returned a 401 JSON body
*unconditionally*, which made the API-vs-browser split written directly below it dead code.
The comment above that return — "for API requests" — states the intent the dead branch was
written to implement. So a human opening ``/chat`` in a browser was served
``{"error": "Unauthorized", ...}`` as raw JSON instead of the login page.

``require_perm`` (``:3486``) had no *unreachable* code but the same defect: an unconditional
401 fallthrough, guarding three pages a human navigates to (``/data``, ``/upload``,
``/admin/database``).

No test referenced either decorator before this file, and ``app.py`` sits at ~18% line
coverage, which is why an unreachable statement survived in an auth path.

Two things these tests deliberately pin, because both are ways the fix could be written
wrong and still look right:

* ``test_sso_redirect_outranks_the_api_split`` — the SSO branch must keep winning for
  ``/api/`` paths. A fix that hoists the API check above the SSO check would still pass every
  browser test while silently dropping the ``anonymous_redirect`` audit event for API callers.
* ``test_json_request_to_a_non_api_path_is_not_redirected`` — the predicate is
  ``is_json or /api/``, not the bare path prefix the dead code used. A JSON client must keep
  getting a machine-readable 401 rather than a 302 to an HTML page it cannot parse.
"""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

import src.interfaces.chat_app.app as app_module
from src.interfaces.chat_app.app import FlaskAppWrapper

UNAUTHORIZED_BODY = {"error": "Unauthorized", "message": "Authentication required"}


def _wrapper(auth_enabled=True, sso_enabled=False):
    """A FlaskAppWrapper carrying only the attributes the decorators read.

    The real constructor builds a Flask app, a pipeline and a DB connection; the decorators
    touch none of that. ``object.__new__`` follows the pattern in
    ``tests/unit/test_chat_refresh_context.py``.
    """
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.auth_enabled = auth_enabled
    wrapper.sso_enabled = sso_enabled
    return wrapper


def _app(wrapper, permission="config:view"):
    """Minimal Flask app exposing one browser route and one API route per decorator."""
    app = Flask(__name__)
    app.secret_key = "test-secret"

    @app.route("/login")
    def login():
        return "login page", 200

    app.add_url_rule("/chat", "chat", wrapper.require_auth(lambda: ("chat page", 200)))
    app.add_url_rule(
        "/api/like",
        "api_like",
        wrapper.require_auth(lambda: ("liked", 200)),
        methods=["POST"],
    )
    app.add_url_rule(
        "/upload",
        "upload",
        wrapper.require_perm(permission)(lambda: ("upload page", 200)),
    )
    app.add_url_rule(
        "/api/upload/file",
        "api_upload",
        wrapper.require_perm(permission)(lambda: ("uploaded", 200)),
        methods=["POST"],
    )
    return app


@pytest.fixture
def client():
    return _app(_wrapper()).test_client()


def _login(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["roles"] = ["admin"]
        sess["user"] = {"email": "someone@example.com"}


class TestBrowserRequestsAreRedirected:
    """The bug: a human navigating to a guarded page got JSON instead of a login page."""

    def test_chat_page_redirects_to_login(self, client):
        response = client.get("/chat")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")

    def test_permission_guarded_page_redirects_to_login(self, client):
        response = client.get("/upload")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")

    def test_redirect_carries_no_json_body(self, client):
        """A 302 whose body is still the error JSON would satisfy a status-only assertion."""
        response = client.get("/chat")

        assert response.get_json(silent=True) is None


class TestApiRequestsKeepTheUnchangedContract:
    """Existing API consumers must not be able to tell this change happened."""

    def test_api_path_still_returns_401(self, client):
        response = client.post("/api/like")

        assert response.status_code == 401
        assert response.get_json() == UNAUTHORIZED_BODY

    def test_permission_guarded_api_path_still_returns_401(self, client):
        response = client.post("/api/upload/file")

        assert response.status_code == 401
        assert response.get_json() == UNAUTHORIZED_BODY

    def test_json_request_to_a_non_api_path_is_not_redirected(self, client):
        """The predicate is ``is_json or /api/`` — a JSON caller gets a parseable answer.

        ``request.is_json`` keys off the mimetype alone, so a GET carrying an
        ``application/json`` content type qualifies without needing a body or a POST route.
        """
        response = client.get("/chat", content_type="application/json")

        assert response.status_code == 401
        assert response.get_json() == UNAUTHORIZED_BODY


class TestSsoRedirectIsPreserved:
    """The one branch that already worked. The fix must not disturb it."""

    def _sso_client(self, allow_anonymous=False):
        return _app(_wrapper(sso_enabled=True)).test_client(), SimpleNamespace(
            allow_anonymous=allow_anonymous
        )

    def test_sso_redirects_and_logs_the_audit_event(self):
        client, registry = self._sso_client()

        with patch.object(app_module, "get_registry", return_value=registry):
            with patch.object(app_module, "log_authentication_event") as logged:
                response = client.get("/chat")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")
        assert logged.call_count == 1
        assert logged.call_args.kwargs["event_type"] == "anonymous_redirect"

    def test_sso_redirect_outranks_the_api_split(self):
        """An API caller under enforced SSO gets the redirect, not the 401.

        This is the assertion that fails if the API check is hoisted above the SSO check —
        a rewrite that passes every browser test while dropping the audit event.
        """
        client, registry = self._sso_client()

        with patch.object(app_module, "get_registry", return_value=registry):
            with patch.object(app_module, "log_authentication_event") as logged:
                response = client.post("/api/like")

        assert response.status_code == 302
        assert logged.call_args.kwargs["event_type"] == "anonymous_redirect"

    def test_sso_allowing_anonymous_falls_through_to_the_split(self):
        client, registry = self._sso_client(allow_anonymous=True)

        with patch.object(app_module, "get_registry", return_value=registry):
            with patch.object(app_module, "log_authentication_event"):
                browser = client.get("/chat")

        assert browser.status_code == 302
        assert browser.headers["Location"].endswith("/login")


class TestAuthenticatedAndDisabledPassThrough:
    """Only the unauthenticated rejection path changes."""

    def test_auth_disabled_invokes_the_route(self):
        client = _app(_wrapper(auth_enabled=False)).test_client()

        assert client.get("/chat").status_code == 200
        assert client.post("/api/like").status_code == 200

    def test_logged_in_session_invokes_the_route(self, client):
        _login(client)

        with patch.object(app_module, "has_permission", return_value=True):
            assert client.get("/chat").status_code == 200
            assert client.get("/upload").status_code == 200

    def test_logged_in_without_permission_still_gets_403(self, client):
        """The 403 path is untouched — a redirect here would be a security regression."""
        _login(client)

        with patch.object(app_module, "has_permission", return_value=False):
            response = client.get("/upload")

        assert response.status_code == 403


def _guard_name(handler):
    """Name of the decorator a handler argument is wrapped in, or None if it is bare.

    Two shapes appear in ``add_all_endpoints``::

        self.require_auth(self.index)                              # -> "require_auth"
        self.require_perm(Permission.Upload.PAGE)(self.upload_page)  # -> "require_perm"
    """
    if not isinstance(handler, ast.Call):
        return None
    func = handler.func
    if isinstance(func, ast.Call):  # require_perm(permission)(handler)
        func = func.func
    return func.attr if isinstance(func, ast.Attribute) else None


def _registered_routes(tree):
    """Map every ``add_endpoint`` route literal to the decorator guarding its handler."""
    routes = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_endpoint"
            and len(node.args) >= 3
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            routes[node.args[0].value] = _guard_name(node.args[2])
    return routes


class TestGuardedPagesAreRegisteredThroughTheDecorators:
    """The decorators only reach a user if the real routes are registered with them.

    Every other test in this file registers its own routes on a bare Flask app, so none of
    them would notice a page that shipped undecorated — the fix would be correct and
    unreachable. Reading the registration calls closes that gap without building the real
    app, whose constructor needs a pipeline and a database.
    """

    # The five browser pages whose rejection response this change alters.
    BROWSER_PAGES = ("/chat", "/terms", "/data", "/upload", "/admin/database")

    @pytest.mark.parametrize("route", BROWSER_PAGES)
    def test_browser_page_is_registered_behind_an_auth_guard(self, route):
        routes = _registered_routes(ast.parse(Path(app_module.__file__).read_text()))

        assert route in routes, f"{route} is no longer registered via add_endpoint"
        assert routes[route] in ("require_auth", "require_perm")

    def test_an_undecorated_page_would_be_caught(self):
        """Keeps the check above from passing vacuously if the call shape ever changes."""
        bare = ast.parse('self.add_endpoint("/chat", "index", self.index)')

        assert _registered_routes(bare) == {"/chat": None}


class TestNoUnreachableStatementRemains:
    """The acceptance criterion from issue #176, as an executable check."""

    def test_decorated_functions_have_no_statement_after_an_unconditional_return(self):
        source = Path(app_module.__file__).read_text()
        tree = ast.parse(source)

        unreachable = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "decorated_function":
                for block in ast.walk(node):
                    body = getattr(block, "body", [])
                    for index, statement in enumerate(body[:-1]):
                        if isinstance(statement, ast.Return):
                            unreachable.append(
                                (statement.lineno, body[index + 1].lineno)
                            )

        assert unreachable == []
