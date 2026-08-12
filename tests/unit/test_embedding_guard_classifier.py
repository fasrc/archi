"""Gated unit tests for the network-vs-local-defect classifier.

Drives tests/support/embedding_guard.py directly with synthetic exceptions. No third-party
import at module scope, no embedding library, no network — see issue #200 and
openspec/changes/fix-issue-200-embedding-guard-unit-tests. This is the gated counterpart to
tests/smoke/test_embedding_benchmarks.py::TestEmbeddingGuard, minus the parts that need the real
embedding library.
"""

import errno
import json
import sys
import types

import pytest

from tests.support.embedding_guard import (
    _GUARDED_ERRORS,
    _NETWORK_ERRNOS,
    _NETWORK_ERROR_TYPES,
    _assert_propagates,
    _import_or_skip,
    _is_network_failure,
    _is_transient_status,
)


def _construct(exc_type):
    """Build a bare instance of *exc_type* without assuming its constructor signature.

    Every currently-allowlisted type accepts a single string message, but the allowlist is built
    from four optional-import blocks (tests/support/embedding_guard.py) and a family added there
    later might not. `__new__` is the fallback for a type that cannot be built any other way; a
    type that raises even from that is skipped rather than failing the whole run over one unrelated
    exception's constructor.
    """
    try:
        return exc_type("synthetic network failure")
    except Exception:
        pass
    try:
        return exc_type()
    except Exception:
        pass
    try:
        return exc_type.__new__(exc_type)
    except Exception:
        pytest.skip(f"cannot construct {exc_type.__module__}.{exc_type.__name__} bare")


def test_every_named_network_type_is_classified_as_a_network_failure():
    """Every entry in _NETWORK_ERROR_TYPES must itself be recognised as a network failure.

    Iterates the tuple rather than retyping the list of names, so a family added to one of the
    four conditional-import blocks is covered automatically.
    """
    assert _NETWORK_ERROR_TYPES, "the allowlist emptied out; nothing left to classify"
    for exc_type in _NETWORK_ERROR_TYPES:
        exc = _construct(exc_type)
        assert _is_network_failure(exc), (
            f"{exc_type.__module__}.{exc_type.__name__} is allowlisted but not classified as a "
            "network failure"
        )


def test_every_named_network_errno_is_classified_as_a_network_failure():
    """Every errno in _NETWORK_ERRNOS is recognised via a plain OSError(errno, ...).

    ENETUNREACH, EHOSTUNREACH and ENETDOWN stay bare OSError in Python — there is no dedicated
    subclass — so this is the branch that has to classify by errno rather than by type.
    """
    assert _NETWORK_ERRNOS, "the errno table emptied out; nothing left to classify"
    for errno_value in _NETWORK_ERRNOS:
        exc = OSError(errno_value, "synthetic network errno failure")
        assert _is_network_failure(
            exc
        ), f"errno {errno_value} is in _NETWORK_ERRNOS but not classified as a network failure"


def _all_subclasses(cls):
    """Every subclass of *cls*, direct or indirect, already loaded into memory."""
    seen = set()
    stack = list(cls.__subclasses__())
    while stack:
        sub = stack.pop()
        if sub in seen:
            continue
        seen.add(sub)
        stack.extend(sub.__subclasses__())
    return seen


def _known_client_side_or_definitive_errors():
    """The client-side and definitive error types the allowlist must never cover.

    Mirrors the list in
    tests/smoke/test_embedding_benchmarks.py::test_no_named_network_type_drags_in_a_local_defect —
    same three-review-round history (HfHubHTTPError, then requests.RequestException, then
    httpx.TransportError). Kept as a second call site of the same invariant rather than importing
    the smoke module, so this gated test's collection can never depend on a suite that itself
    imports the embedding library.
    """
    defects: list[type[BaseException]] = []
    try:
        from requests import exceptions as requests_exc

        defects += [
            requests_exc.InvalidURL,
            requests_exc.MissingSchema,
            requests_exc.InvalidSchema,
            requests_exc.TooManyRedirects,
            requests_exc.URLRequired,
        ]
    except ImportError:
        pass
    try:
        import httpx

        defects += [httpx.UnsupportedProtocol, httpx.LocalProtocolError]
    except ImportError:
        pass
    try:
        from huggingface_hub import errors as hub_errors

        defects += [
            hub_errors.RepositoryNotFoundError,
            hub_errors.GatedRepoError,
            hub_errors.DisabledRepoError,
            hub_errors.RevisionNotFoundError,
            hub_errors.BadRequestError,
        ]
    except ImportError:
        pass
    return defects


def test_no_allowlisted_type_covers_a_client_side_or_definitive_error_via_subclassing():
    """The allowlist invariant, gated: walk __subclasses__() instead of checking issubclass().

    test_no_named_network_type_drags_in_a_local_defect (tests/smoke/) asserts the same invariant by
    checking each known local defect against each allowlisted type. This walks the other
    direction — from each allowlisted type down through its already-imported subclasses — so a
    defect that the allowlist has quietly started covering is caught by set membership. Either
    direction proves the same thing; this one names both the allowlisted type and the offending
    subclass, matching the smoke test's failure message shape.
    """
    local_defects = set(_known_client_side_or_definitive_errors())
    if not local_defects:
        pytest.skip("no optional third-party HTTP library installed to audit")

    for named in _NETWORK_ERROR_TYPES:
        offending = _all_subclasses(named) & local_defects
        assert not offending, (
            f"{named.__module__}.{named.__name__} allowlists "
            f"{', '.join(sorted(f'{d.__module__}.{d.__name__}' for d in offending))} via "
            "__subclasses__(), so a client-side or definitive failure would be reported as a "
            "network outage. Name the transport families individually instead of their shared base."
        )


@pytest.mark.parametrize("status", [401, 403, 404, 410])
def test_a_definitive_client_status_is_not_a_network_failure(status):
    """A 401/403/404/410 is a definitive answer about a broken model dependency, not an outage.

    Mirrors tests/smoke/test_embedding_benchmarks.py::test_a_definitive_client_status_is_not_an_outage,
    but stays hermetic (design D5): _is_network_failure only reads `.response.status_code` via
    getattr, so a bare types.SimpleNamespace exercises the branch exactly as a real
    requests.Response would, with no requests import.
    """
    exc = Exception(f"{status} Client Error")
    exc.response = types.SimpleNamespace(status_code=status)
    assert not _is_network_failure(
        exc
    ), f"status {status} is a definitive client answer but was classified as a network failure"


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503, 520, 524])
def test_a_transient_or_server_side_status_is_a_network_failure(status):
    """The statuses that mean "reached, but cannot serve it now" are network failures.

    Mirrors tests/smoke/test_embedding_benchmarks.py::test_a_transient_hub_status_still_skips and
    test_any_server_side_status_is_an_outage: 408/425 are request-timing, 429 is rate limiting, and
    520/524 are Cloudflare's own origin-trouble codes fronting the Hub — the exact shape of the
    #187 incident. All must count as an outage, not a definitive answer.
    """
    exc = Exception(f"{status} Server Error")
    exc.response = types.SimpleNamespace(status_code=status)
    assert _is_network_failure(exc), (
        f"status {status} means the host could not serve the weights right now but was not "
        "classified as a network failure"
    )


@pytest.mark.parametrize("status", [200, 206])
def test_a_success_status_falls_through_to_type_classification(status):
    """A success status on a failing call is not an answer; type classification decides.

    Mirrors tests/smoke/test_embedding_benchmarks.py::test_a_truncated_download_still_skips:
    requests attaches the successful response to a mid-stream failure like ChunkedEncodingError, so
    a 2xx status means the transfer started and then dropped, not that the server answered
    definitively. Only an ERROR status (>= 400) is decisive at step 1 of _is_network_failure; a
    success status must fall through to the type/errno checks in step 3. Using a builtin
    ConnectionError (always allowlisted, no third-party import needed) makes the correct answer
    True — a regression that treated "has a status" as decisive regardless of range would read the
    200 as a definitive reply and wrongly return False here.
    """
    exc = ConnectionError("connection broken: incomplete read")
    exc.response = types.SimpleNamespace(status_code=status)
    assert _is_network_failure(exc), (
        f"status {status} is a success status on a network-type exception, but was not classified "
        "as a network failure — it looks like the success status was read as a definitive answer"
    )


def _guarded_call(to_raise: BaseException):
    """Mirror _load_model's catch-classify-reraise-or-skip shape, hermetically.

    tests/smoke/test_embedding_benchmarks.py::_load_model needs the third-party embedding
    library installed to construct the model that raises. This drives the exact same contract —
    ``except _GUARDED_ERRORS: if not _is_network_failure(exc): raise`` — directly against a
    synthetic exception, so the contract is gated without the embedding library.
    """
    try:
        raise to_raise
    except _GUARDED_ERRORS as exc:
        if not _is_network_failure(exc):
            raise
        pytest.skip(f"embedding weights unreachable over the network: {exc!r}")


def test_an_assertion_error_is_not_converted_to_skip():
    """AssertionError is not in _GUARDED_ERRORS at all, so it must propagate untouched.

    Gated counterpart to test_assertion_error_is_not_converted_to_skip (tests/smoke/): an
    embedding regression must fail the benchmark, not be swallowed into a network skip.
    """
    _assert_propagates(
        AssertionError,
        lambda: _guarded_call(
            AssertionError("embedding regression, not a network problem")
        ),
        match="embedding regression",
    )


def test_a_full_disk_oserror_is_not_converted_to_skip():
    """OSError(ENOSPC) is caught by the OSError branch of _GUARDED_ERRORS but carries no network errno.

    Gated counterpart to test_a_local_oserror_is_not_reported_as_a_network_outage (tests/smoke/):
    a full disk must fail the benchmark, not be misreported as a CDN outage.
    """
    _assert_propagates(
        OSError,
        lambda: _guarded_call(OSError(errno.ENOSPC, "No space left on device")),
    )


def test_a_permission_error_is_not_converted_to_skip():
    """PermissionError is an OSError subclass without a network errno, so it must propagate.

    Gated counterpart to the same smoke test: a permission failure on the model cache directory
    must fail the benchmark, not be misreported as a CDN outage.
    """
    _assert_propagates(
        PermissionError,
        lambda: _guarded_call(
            PermissionError(13, "Permission denied", "~/.cache/huggingface/hub")
        ),
    )


def test_is_transient_status_treats_5xx_as_a_range():
    """A 5xx code no list enumerates must still count, because the check is a range.

    Enumerating the familiar four (500/502/503/504) is exactly the defect this file's docstring
    warns about: the next vendor-invented code, such as 599 here, would fall through a fixed list.
    ``status >= 500`` cannot develop that gap.
    """
    assert _is_transient_status(599)


@pytest.fixture
def broken_transitive_import(tmp_path, monkeypatch):
    """A real, importable module whose OWN top-level import statement fails.

    Mirrors tests/smoke/test_embedding_benchmarks.py::test_a_broken_transitive_import_is_not_reported_as_missing,
    but drives the actual import machinery instead of monkeypatching importlib.import_module: the
    module file is placed on sys.path so import_module finds and executes it, and Python's own
    import statement inside raises ModuleNotFoundError naming a DIFFERENT, unrelated module — the
    exact shape _import_or_skip must not mistake for its own target module being absent.
    """
    module_name = "embedding_guard_test_broken_transitive_dep"
    (tmp_path / f"{module_name}.py").write_text(
        "import some_unrelated_transitive_dep_that_does_not_exist\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    yield module_name
    sys.modules.pop(module_name, None)


def test_import_or_skip_returns_the_attribute_for_an_installed_module():
    """A genuinely installed module's requested attribute comes back untouched.

    Uses a stdlib module so the test needs no optional dependency: json is always importable.
    """
    assert _import_or_skip("json", "dumps") is json.dumps


def test_import_or_skip_skips_naming_a_genuinely_absent_module():
    """A module that is not installed at all produces a skip naming it, not an error."""
    with pytest.raises(pytest.skip.Exception) as exc_info:
        _import_or_skip(
            "embedding_guard_test_genuinely_absent_module", "whatever_attribute"
        )
    assert "embedding_guard_test_genuinely_absent_module" in str(exc_info.value)


def test_import_or_skip_propagates_a_broken_transitive_import(
    broken_transitive_import,
):
    """A ModuleNotFoundError raised from INSIDE an installed module must propagate, not skip.

    Gated counterpart to test_a_broken_transitive_import_is_not_reported_as_missing (tests/smoke/):
    the requested module (broken_transitive_import) IS present on sys.path, so import_module finds
    it; it is the module's own import of an unrelated, missing dependency that fails. Reporting that
    as "broken_transitive_import not installed" would hide a real broken environment behind a skip.
    """
    _assert_propagates(
        ModuleNotFoundError,
        lambda: _import_or_skip(broken_transitive_import, "whatever_attribute"),
        match="some_unrelated_transitive_dep_that_does_not_exist",
    )
