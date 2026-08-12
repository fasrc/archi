"""Network-vs-local-defect classifier for the embedding benchmark guard.

Shared by tests/unit/ (gated, network-free) and tests/smoke/ (opt-in,
CDN-reaching) — see issue #187 and issue #200. Names stay private (leading
underscore) because this is a test-support seam, not a public API.
"""

import errno
import importlib
import socket

import pytest

# Exception TYPES that always mean "the weights could not be fetched", whatever
# errno they carry. Each optional family is appended only when its library is
# importable, so the tuple stays valid in a minimal environment:
#   - The requests TRANSPORT families are named one by one, deliberately NOT
#     their shared base RequestException. That base is also the base of
#     InvalidURL, MissingSchema and TooManyRedirects — a malformed HF_ENDPOINT
#     or a redirect loop, which are local misconfiguration and a definitive
#     server answer respectively. Neither carries a >= 400 response, so the
#     status check cannot filter them and naming the base turned both into a
#     green skip claiming the network was unreachable. Naming the families is
#     still required: requests.ConnectionError is NOT a builtin ConnectionError
#     (its MRO is RequestException → OSError) and its errno is None, so neither
#     the builtin type nor the errno table below would catch it. SSLError,
#     ProxyError and ConnectTimeout arrive via ConnectionError/Timeout.
#   - XetDownloadError subclasses Exception DIRECTLY (not OSError, not
#     RequestException, not httpx), and the Xet CDN — cas-server.xethub.hf.co —
#     is the transport issue #187 was reported against, so it must be named.
#     Kept in its own try block so an older hub without it still contributes
#     the other error types.
#   - The httpx TRANSPORT families are likewise named individually, NOT their
#     base TransportError, which also covers UnsupportedProtocol (a malformed
#     HF_ENDPOINT on the httpx transport) and LocalProtocolError (a header WE
#     sent is illegal). RemoteProtocolError is included, because there the
#     server broke the protocol mid-transfer; its sibling LocalProtocolError is
#     not. No httpx exception is an OSError subclass, so naming is required.
#   - HfHubHTTPError is deliberately ABSENT. Six of its seven subclasses —
#     RepositoryNotFoundError, GatedRepoError, DisabledRepoError,
#     RevisionNotFoundError, EntryNotFoundError, BadRequestError — are
#     definitive answers about a broken model dependency, and they carry a
#     response, so the status check above is their correct classifier. Only
#     LocalEntryNotFoundError means unreachable, and it is named directly. A hub
#     HTTP error carrying NO response is handled by its own clause in
#     _is_network_failure: with no status there is nothing to classify, and
#     hf_raise_for_status always attaches one, so that shape only arises when
#     the request never completed.
#
# Every entry here is an ALLOWLIST member: it must mean "no usable answer came
# back", for itself AND for all of its subclasses. Naming a base class that also
# covers client-side defects is the defect this file hit three review rounds in a
# row (HfHubHTTPError, then requests.RequestException, then httpx.TransportError),
# so test_no_named_network_type_drags_in_a_local_defect now fails the suite if a
# fourth one is added.
_NETWORK_ERROR_TYPES: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    socket.gaierror,
)
try:
    from requests.exceptions import ChunkedEncodingError
    from requests.exceptions import ConnectionError as RequestsConnectionError
    from requests.exceptions import ContentDecodingError, RetryError
    from requests.exceptions import Timeout as RequestsTimeout

    _NETWORK_ERROR_TYPES += (
        RequestsConnectionError,
        RequestsTimeout,
        ChunkedEncodingError,
        ContentDecodingError,
        RetryError,
    )
except ImportError:
    pass
_HUB_HTTP_ERROR: type[BaseException] | None = None
try:
    from huggingface_hub.errors import (
        HfHubHTTPError,
        LocalEntryNotFoundError,
        OfflineModeIsEnabled,
    )

    _HUB_HTTP_ERROR = HfHubHTTPError
    _NETWORK_ERROR_TYPES += (LocalEntryNotFoundError, OfflineModeIsEnabled)
except ImportError:
    pass
try:
    from huggingface_hub.errors import XetDownloadError

    _NETWORK_ERROR_TYPES += (XetDownloadError,)
except ImportError:
    pass
try:
    from httpx import NetworkError as HttpxNetworkError
    from httpx import ProxyError as HttpxProxyError
    from httpx import RemoteProtocolError as HttpxRemoteProtocolError
    from httpx import TimeoutException as HttpxTimeoutException

    _NETWORK_ERROR_TYPES += (
        HttpxTimeoutException,
        HttpxNetworkError,
        HttpxProxyError,
        HttpxRemoteProtocolError,
    )
except ImportError:
    pass

# Network failures that arrive as a PLAIN OSError. Python maps only a few errnos
# to dedicated subclasses (ECONNREFUSED → ConnectionRefusedError, ETIMEDOUT →
# TimeoutError); ENETUNREACH, EHOSTUNREACH and ENETDOWN stay bare OSError, so a
# type-only guard would let a genuine outage fail the benchmark.
_NETWORK_ERRNOS = frozenset(
    {
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTDOWN,
        errno.EHOSTUNREACH,
        errno.ENETDOWN,
        errno.ENETRESET,
        errno.ENETUNREACH,
        errno.ENOTCONN,
        errno.EPIPE,
        errno.ETIMEDOUT,
    }
)

# HTTP statuses that mean "the Hub was reached but cannot serve the weights right
# now". Everything else it answers with — 401, 403, 404, 410 — is a definitive
# reply about a broken model dependency, not an outage, and must reach the
# developer as a failure. 408/425 are request-timing and 429 is rate limiting;
# all clear on a retry, none indicate the benchmark is wrong.
_TRANSIENT_4XX_STATUSES = frozenset({408, 425, 429})


def _is_transient_status(status: int) -> bool:
    """True when *status* means the host could not serve the weights right now.

    Any 5xx counts, expressed as a RANGE rather than a list. Enumerating the
    familiar four (500/502/503/504) left out the codes an outage most often
    actually arrives as: HuggingFace sits behind Cloudflare, whose origin-trouble
    statuses are 520/521/522/524, and a 507 or any other server-side code was in
    the same gap. Those were failing the deliberate benchmark while the developer
    guide promised that a server-side 5xx skips. A range cannot develop that gap
    again when a vendor invents the next code.
    """
    return status >= 500 or status in _TRANSIENT_4XX_STATUSES


# What _load_model actually catches: a named, bounded set. NEVER bare Exception —
# that would absorb an AssertionError from a real embedding regression and make
# the benchmark incapable of failing (see design D4, and the spec requirement
# "The network guard names specific exception types"). OSError is in the set so
# the plain-OSError errnos above are reachable; it is then classified by errno,
# so PermissionError on the model cache, ENOSPC on a full disk and a corrupt
# cache directory still propagate as the local defects they are.
_GUARDED_ERRORS: tuple[type[BaseException], ...] = _NETWORK_ERROR_TYPES + (OSError,)


def _is_network_failure(exc: BaseException) -> bool:
    """True when *exc* means the weights could not be fetched over the network.

    Three questions, in this order, because each is more authoritative than the
    next:

    1. Did the host answer with an error status? Then the status decides. A 401,
       403 or 404 means it answered definitively about a broken model
       dependency, and calling that an outage would turn a renamed or gated
       repository into a permanent green-by-skip — the benchmark would stop
       exercising its only model dependency and never say so.
    2. Is it a bare hub HTTP error with no response at all? Then the request
       never completed, so there is no answer to classify and it is a transport
       failure. hf_raise_for_status always attaches a response, so that shape
       only arises when nothing came back — the #187 shape. This tests the EXACT
       type, not isinstance: the base class alone means only "an HTTP error
       happened", while every subclass names something specific, and the
       specific ones — RepositoryNotFoundError, GatedRepoError and the rest —
       are definitive even if someone constructs one without a response.
       LocalEntryNotFoundError is a subclass, so it falls through to step 3
       where it is named directly.
    3. Otherwise, is the type in the transport allowlist, or a plain OSError
       carrying a network errno?

    Only an ERROR status is decisive at step 1. requests attaches the successful
    response to a mid-stream failure like ChunkedEncodingError, so a 2xx with an
    exception is a dropped transfer, not an answer, and falls through.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None and status >= 400:
        return _is_transient_status(status)
    if _HUB_HTTP_ERROR is not None and type(exc) is _HUB_HTTP_ERROR:
        return getattr(exc, "response", None) is None
    if isinstance(exc, _NETWORK_ERROR_TYPES):
        return True
    return isinstance(exc, OSError) and exc.errno in _NETWORK_ERRNOS


def _response(status: int):
    """A minimal requests.Response carrying *status*, for the HTTP guard tests."""
    requests_mod = pytest.importorskip("requests")
    response = requests_mod.Response()
    response.status_code = status
    return response


def _assert_propagates(expected: type[BaseException], call, match: str = ""):
    """Assert *call* raises *expected*, failing LOUDLY if it skips instead.

    Every negative test here checks that something is NOT converted into a skip,
    so a bare `pytest.raises(expected)` is the wrong tool: when the guard does
    swallow the error, pytest's Skipped exception propagates out of the
    `raises` block and the test reports as SKIPPED — which reads as green in CI.
    A regression guard that goes quiet instead of red is barely a guard, so the
    skip is caught and turned into an explicit failure.
    """
    try:
        call()
    except expected as exc:
        if match:
            assert match in str(exc)
    except pytest.skip.Exception as exc:
        pytest.fail(f"the guard swallowed {expected.__name__} into a skip: {exc}")
    else:
        pytest.fail(f"{expected.__name__} was not raised, and nothing skipped")


def _import_or_skip(module: str, attr: str):
    """Import *attr* from *module*, or skip naming the missing library.

    Mirrors _load_model's missing-library contract for the benchmark's other
    imports. The original test wrapped the model and splitter imports in one
    ``except ImportError``; when the model import moved into _load_model, the
    splitter and Document imports were left bare, so a missing dependency
    raised ModuleNotFoundError and errored the documented benchmark command
    instead of reporting a skip.

    Only a genuinely ABSENT module skips. An ImportError raised from inside an
    installed module — a missing or incompatible transitive dependency — is a
    broken environment, and reporting it as "not installed" would hide a real
    defect behind a skip.
    """
    try:
        loaded = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing and (module == missing or module.startswith(f"{missing}.")):
            pytest.skip(f"{module} not installed")
        raise
    return getattr(loaded, attr)
