"""Gated unit tests for the network-vs-local-defect classifier.

Drives tests/support/embedding_guard.py directly with synthetic exceptions. No third-party
import at module scope, no embedding library, no network — see issue #200 and
openspec/changes/fix-issue-200-embedding-guard-unit-tests. This is the gated counterpart to
tests/smoke/test_embedding_benchmarks.py::TestEmbeddingGuard, minus the parts that need the real
embedding library.
"""

import types

import pytest

from tests.support.embedding_guard import (
    _NETWORK_ERRNOS,
    _NETWORK_ERROR_TYPES,
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


def test_is_transient_status_treats_5xx_as_a_range():
    """A 5xx code no list enumerates must still count, because the check is a range.

    Enumerating the familiar four (500/502/503/504) is exactly the defect this file's docstring
    warns about: the next vendor-invented code, such as 599 here, would fall through a fixed list.
    ``status >= 500`` cannot develop that gap.
    """
    assert _is_transient_status(599)
