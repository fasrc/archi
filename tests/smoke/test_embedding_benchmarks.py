"""
Embedding benchmark tests that reach the HuggingFace CDN.

These tests are deliberately excluded from the gating suite (tests/unit/) so
that a CDN outage cannot red a pull request — see issue #187. They live here
in tests/smoke/ where they are opt-in for developers who need to verify the
embedding pipeline locally or in a post-merge environment.

Run with:
    python -m pytest tests/smoke/test_embedding_benchmarks.py -v

Requires:
  - langchain_huggingface installed
  - HuggingFace CDN reachable (downloads ~90 MB of model weights on first run)
  - 30-50 seconds per test on CPU
"""

import errno
import importlib
import socket

import pytest

# Exception TYPES that always mean "the weights could not be fetched", whatever
# errno they carry. Each optional family is appended only when its library is
# importable, so the tuple stays valid in a minimal environment:
#   - requests.RequestException covers requests.ConnectionError/Timeout, and
#     huggingface_hub's HfHubHTTPError, which subclasses it. Note that
#     requests.ConnectionError is NOT a builtin ConnectionError — its MRO is
#     RequestException → IOError — so naming the builtin alone would miss it.
#   - XetDownloadError subclasses Exception DIRECTLY (not OSError, not
#     RequestException, not httpx), and the Xet CDN — cas-server.xethub.hf.co —
#     is the transport issue #187 was reported against, so it must be named.
#     Kept in its own try block so an older hub without it still contributes
#     the other error types.
#   - httpx.TransportError covers ConnectError/TimeoutException/ReadError; no
#     httpx exception is an OSError subclass, so they need naming too.
_NETWORK_ERROR_TYPES: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    socket.gaierror,
)
try:
    from requests.exceptions import RequestException

    _NETWORK_ERROR_TYPES += (RequestException,)
except ImportError:
    pass
try:
    from huggingface_hub.errors import (
        HfHubHTTPError,
        LocalEntryNotFoundError,
        OfflineModeIsEnabled,
    )

    _NETWORK_ERROR_TYPES += (
        HfHubHTTPError,
        LocalEntryNotFoundError,
        OfflineModeIsEnabled,
    )
except ImportError:
    pass
try:
    from huggingface_hub.errors import XetDownloadError

    _NETWORK_ERROR_TYPES += (XetDownloadError,)
except ImportError:
    pass
try:
    from httpx import TransportError

    _NETWORK_ERROR_TYPES += (TransportError,)
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
# developer as a failure. 408/425 are request-timing, 429 is rate limiting, 5xx
# is the server's own trouble; all clear on a retry, none indicate the benchmark
# is wrong.
_TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

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

    An HTTP ERROR status is checked FIRST, and it is decisive. Every
    huggingface_hub HTTP error subclasses HfHubHTTPError → RequestException, so
    the type test below matches all of them unconditionally — including 401, 403
    and 404, where the Hub was reached and answered definitively. Calling those
    an outage would turn a renamed, removed or gated model repository into a
    permanent green-by-skip: the benchmark would stop exercising its only model
    dependency and never say so.

    Only an error status is decisive, though. requests attaches the successful
    response to a mid-stream failure like ChunkedEncodingError, so a 2xx with an
    exception is a dropped transfer, not an answer — those fall through to the
    type and errno tests. So does an error with no response at all, which is the
    #187 CDN case: nothing came back to read a status from.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None and status >= 400:
        return status in _TRANSIENT_HTTP_STATUSES
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


def _load_model(model_name: str):
    """Load a HuggingFaceEmbeddings model or skip with a reason that names the failure.

    - Missing library → skip("langchain_huggingface not installed")
    - Network/offline error → skip("embedding weights unreachable over the network …")
    - Any other exception (including AssertionError) propagates unchanged.
    """
    try:
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(model_name=model_name)
    except ImportError:
        pytest.skip("langchain_huggingface not installed")
    except _GUARDED_ERRORS as exc:
        if not _is_network_failure(exc):
            raise
        pytest.skip(
            f"embedding weights unreachable over the network ({model_name}): {exc!r}"
        )


class TestEmbeddingBenchmarks:
    def test_embedding_model_works(self):
        """
        HYPOTHESIS: The embedding model fails silently.

        This tests that the HuggingFace embedding model can actually
        generate embeddings.

        NOTE: This also serves as a performance benchmark. On CPU,
        embedding is very slow (30-50+ seconds per file).
        """
        import time

        model = _load_model("sentence-transformers/all-MiniLM-L6-v2")

        test_texts = ["This is a test document.", "Another test."]

        start = time.time()
        embeddings = model.embed_documents(test_texts)
        elapsed = time.time() - start

        assert len(embeddings) == 2, "Should generate 2 embeddings"
        assert len(embeddings[0]) == 384, "Embedding dimension should be 384"

        print(f"Embedding 2 short texts took {elapsed:.2f} seconds")

        # Test with longer text (more realistic)
        long_text = "This is a longer test document. " * 100
        start = time.time()
        _embeddings = model.embed_documents([long_text])
        elapsed = time.time() - start
        print(f"Embedding 1 long text took {elapsed:.2f} seconds")

    def test_embedding_performance_realistic(self):
        """
        Performance test for realistic HTML content embedding.

        This test measures how long it takes to embed content similar to
        scraped web pages to identify performance bottlenecks.
        """
        import time

        CharacterTextSplitter = _import_or_skip(
            "langchain_text_splitters.character", "CharacterTextSplitter"
        )
        Document = _import_or_skip("langchain_core.documents", "Document")

        model = _load_model("sentence-transformers/all-MiniLM-L6-v2")
        splitter = CharacterTextSplitter(chunk_size=500, chunk_overlap=50)

        # Simulate a ~65KB HTML page (typical scraped page size)
        html_content = (
            """
        <html><head><title>Test Page</title></head><body>
        <h1>Welcome to the Test Page</h1>
        <p>This is paragraph content that represents typical web page text.
        It contains various sentences and information that would be found
        on a real website about computing, research, or education.</p>
        """
            * 500
        )  # ~65KB

        # Time the chunking
        start = time.time()
        doc = Document(page_content=html_content, metadata={})
        chunks = splitter.split_documents([doc])
        chunk_time = time.time() - start

        # Time the embedding
        chunk_texts = [c.page_content for c in chunks]
        start = time.time()
        _embeddings = model.embed_documents(chunk_texts)
        embed_time = time.time() - start

        print(f"\n=== PERFORMANCE RESULTS ===")
        print(f"Content size: {len(html_content)} bytes")
        print(f"Chunks generated: {len(chunks)}")
        print(f"Chunking time: {chunk_time:.2f}s")
        print(f"Embedding time: {embed_time:.2f}s")
        print(f"Time per chunk: {embed_time/len(chunks):.2f}s")
        print(
            f"Estimated time for 46 files (3 chunks each): {46 * 3 * embed_time/len(chunks) / 60:.1f} minutes"
        )

        # Warn if embedding is too slow
        if embed_time > 30:
            print(
                f"\nWARNING: Embedding took {embed_time:.0f}s - consider GPU acceleration!"
            )


class TestTheGuardTestsDegradeLikeTheCodeTheyGuard:
    """The guard tests must honour the same contract they police.

    `_load_model` promises "langchain_huggingface not installed → skip", and
    `_import_or_skip` extends that to the benchmark's other imports. But every
    guard test patches "langchain_huggingface.HuggingFaceEmbeddings.__init__" by
    STRING, and pytest resolves a string target by importing it — so in a
    minimal environment the setattr raised ModuleNotFoundError before the skip
    could ever apply.

    Measured before the fix, running the documented command with the library
    made unimportable: **6 failed, 2 passed, 2 skipped**. The two skips were the
    benchmarks, which honour the contract; the six failures were the tests whose
    whole job is to keep the contract honest.
    """

    def test_the_guard_tests_skip_when_the_model_library_is_absent(self, tmp_path):
        import os
        import subprocess
        import sys

        # A meta_path hook is the only faithful way to make an INSTALLED package
        # unimportable for a child process: uninstalling is not available to a
        # test, and deleting sys.modules entries would not stop a fresh import.
        (tmp_path / "sitecustomize.py").write_text(
            "import sys\n"
            "class _Blocker:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] == 'langchain_huggingface':\n"
            "            raise ModuleNotFoundError(\n"
            "                \"No module named 'langchain_huggingface'\",\n"
            "                name='langchain_huggingface',\n"
            "            )\n"
            "        return None\n"
            "sys.meta_path.insert(0, _Blocker())\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(tmp_path), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)

        # -k selects only TestEmbeddingGuard, which excludes THIS class, so the
        # child cannot re-enter here. It also excludes the two benchmarks, so no
        # subprocess ever reaches for the CDN.
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(__file__),
                "-k",
                "TestEmbeddingGuard",
                "-p",
                "no:cacheprovider",
                "-q",
                "--no-header",
                "-rs",
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            timeout=300,
        )

        assert proc.returncode == 0, (
            "the guard tests did not survive a missing langchain_huggingface:\n"
            f"{proc.stdout[-4000:]}\n{proc.stderr[-2000:]}"
        )
        assert "langchain_huggingface not installed" in proc.stdout, (
            "expected the library-absent skip reason, got:\n" f"{proc.stdout[-4000:]}"
        )


class TestEmbeddingGuard:
    """Guard and negative tests for the network-skip helper.

    These run without a network connection — they monkeypatch the constructor
    so the CDN is never reached. Their purpose is to keep the guard honest
    across refactors: task 2.3 (red) proves the guard works; task 2.5
    (negative) proves it does not swallow AssertionError.
    """

    @pytest.fixture(autouse=True)
    def _require_the_model_library(self):
        """Skip this class when langchain_huggingface is absent.

        Not decoration: the string form of monkeypatch.setattr below resolves
        its target by IMPORTING it (_pytest.monkeypatch.derive_importpath →
        importlib.import_module), so without this the setattr raises
        ModuleNotFoundError during the test body and the documented benchmark
        command reports failures where `_load_model` would have reported a skip.
        Importing the package is local and network-free — only constructing
        HuggingFaceEmbeddings reaches the CDN — so this cannot reintroduce the
        #187 stall. Reuses _import_or_skip so the reason reads the same as every
        other missing-library skip in this file, and so a package that is present
        but broken still surfaces instead of being rebranded as absent.
        """
        _import_or_skip("langchain_huggingface", "HuggingFaceEmbeddings")

    def test_network_guard_converts_connection_error_to_skip(self, monkeypatch):
        """Guard test (task 2.3): a CDN ConnectionError becomes a named skip.

        This reproduces issue #187 deterministically. Before the fix, the
        ConnectionError escaped the ``except ImportError`` guard and failed the
        test. After the fix it is caught and re-raised as pytest.skip.
        """

        def fake_init(self, model_name, **kwargs):
            raise ConnectionError(
                "Network error: Request middleware error: error sending request for url "
                "(https://cas-server.xethub.hf.co/v2/reconstructions/abc)"
            )

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        with pytest.raises(pytest.skip.Exception) as exc_info:
            _load_model("sentence-transformers/all-MiniLM-L6-v2")

        assert "network" in str(exc_info.value).lower()

    def test_assertion_error_is_not_converted_to_skip(self, monkeypatch):
        """Negative test (task 2.5): AssertionError propagates OUT of _load_model.

        The AssertionError must be raised from INSIDE _load_model's try block,
        because that is the only place the guard could swallow it. An earlier
        version of this test asserted on a wrong embedding dimension *after*
        _load_model had returned — outside the helper — so it would still have
        passed with the guard widened to ``except Exception``, which is exactly
        the regression it claims to catch.
        """

        def fake_init(self, model_name, **kwargs):
            raise AssertionError("embedding regression, not a network problem")

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        _assert_propagates(
            AssertionError,
            lambda: _load_model("sentence-transformers/all-MiniLM-L6-v2"),
            match="embedding regression",
        )

    def test_a_local_oserror_is_not_reported_as_a_network_outage(self, monkeypatch):
        """A local filesystem failure must FAIL the benchmark, not skip it.

        Anchoring the guard on the whole OSError hierarchy made every local
        failure look like a CDN outage: PermissionError on the model cache, a
        full disk, an unreadable/corrupt cache directory are all OSError
        subclasses. Those are precisely the non-network defects _load_model
        promises to propagate.
        """

        def fake_init(self, model_name, **kwargs):
            raise PermissionError(13, "Permission denied", "~/.cache/huggingface/hub")

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        _assert_propagates(
            PermissionError,
            lambda: _load_model("sentence-transformers/all-MiniLM-L6-v2"),
        )

    def test_an_httpx_transport_error_becomes_a_named_skip(self, monkeypatch):
        """httpx transport errors are not OSError subclasses.

        When huggingface_hub runs on its httpx transport, a connection failure
        or timeout after its retries surfaces as httpx.ConnectError /
        httpx.TimeoutException. Neither inherits from OSError, so an
        OSError-anchored guard let them fail the benchmark instead of skipping.
        """
        httpx = pytest.importorskip("httpx")

        def fake_init(self, model_name, **kwargs):
            raise httpx.ConnectError("All connection attempts failed")

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        with pytest.raises(pytest.skip.Exception) as exc_info:
            _load_model("sentence-transformers/all-MiniLM-L6-v2")

        assert "network" in str(exc_info.value).lower()

    def test_a_plain_oserror_network_errno_still_skips(self, monkeypatch):
        """A network failure with no dedicated exception subclass still skips.

        ENETUNREACH, EHOSTUNREACH and ENETDOWN all surface as a *plain* OSError
        — Python only maps a handful of errnos (ECONNREFUSED, ECONNRESET, …) to
        dedicated ConnectionError subclasses. Enumerating exception types alone
        therefore missed real outages, which is why OSError is still caught and
        then classified by errno rather than being excluded outright.
        """
        import errno as errno_mod

        def fake_init(self, model_name, **kwargs):
            raise OSError(errno_mod.ENETUNREACH, "Network is unreachable")

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        with pytest.raises(pytest.skip.Exception) as exc_info:
            _load_model("sentence-transformers/all-MiniLM-L6-v2")

        assert "network" in str(exc_info.value).lower()

    def test_a_xet_download_error_becomes_a_named_skip(self, monkeypatch):
        """The Xet CDN family subclasses Exception directly, not OSError.

        Issue #187's own reproduction names `cas-server.xethub.hf.co`, so this
        is the exact transport the incident came through. XetDownloadError
        inherits straight from Exception — it is not an OSError, not a
        RequestException and not an httpx error — so it has to be named.
        """
        errors = pytest.importorskip("huggingface_hub.errors")
        xet_error = getattr(errors, "XetDownloadError", None)
        if xet_error is None:
            pytest.skip("this huggingface_hub has no XetDownloadError")

        def fake_init(self, model_name, **kwargs):
            raise xet_error("failed to download from cas-server.xethub.hf.co")

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        with pytest.raises(pytest.skip.Exception) as exc_info:
            _load_model("sentence-transformers/all-MiniLM-L6-v2")

        assert "network" in str(exc_info.value).lower()

    def test_a_missing_model_repository_fails_instead_of_skipping(self, monkeypatch):
        """A 404 from the Hub is an answer, not an outage.

        Every huggingface_hub HTTP error subclasses HfHubHTTPError, which is in
        _NETWORK_ERROR_TYPES, so a type-only classifier called all of them
        "unreachable" — including the ones where the server was reached and
        replied definitively. If this model repository were renamed or removed,
        both benchmarks would skip forever and report green while their only
        model dependency was broken, which is the same going-quiet failure
        _assert_propagates exists to prevent.
        """
        errors = pytest.importorskip("huggingface_hub.errors")

        def fake_init(self, model_name, **kwargs):
            raise errors.RepositoryNotFoundError(
                "404 Client Error: Not Found", response=_response(404)
            )

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        _assert_propagates(
            errors.RepositoryNotFoundError,
            lambda: _load_model("sentence-transformers/all-MiniLM-L6-v2"),
        )

    def test_a_gated_model_repository_fails_instead_of_skipping(self, monkeypatch):
        """403 on a gated repo is a credentials problem, not a network problem.

        Skipping here would hide a missing or expired HF token behind a message
        that sends the developer to look at their connection.
        """
        errors = pytest.importorskip("huggingface_hub.errors")

        def fake_init(self, model_name, **kwargs):
            raise errors.GatedRepoError(
                "403 Client Error: Forbidden", response=_response(403)
            )

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        _assert_propagates(
            errors.GatedRepoError,
            lambda: _load_model("sentence-transformers/all-MiniLM-L6-v2"),
        )

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 429, 408])
    def test_a_transient_hub_status_still_skips(self, monkeypatch, status):
        """The statuses that DO mean "reached, but cannot serve it now" still skip.

        Distinguishing definitive answers from transient ones is the whole point;
        narrowing the guard must not swing the other way and start failing the
        benchmark on a Hub hiccup.
        """
        errors = pytest.importorskip("huggingface_hub.errors")

        def fake_init(self, model_name, **kwargs):
            raise errors.HfHubHTTPError(
                f"{status} Server Error", response=_response(status)
            )

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        with pytest.raises(pytest.skip.Exception) as exc_info:
            _load_model("sentence-transformers/all-MiniLM-L6-v2")

        assert "network" in str(exc_info.value).lower()

    def test_a_hub_error_carrying_no_response_still_skips(self, monkeypatch):
        """No status means the server never answered — a transport failure.

        HfHubHTTPError's `response` is optional, and the CDN case from #187 is
        exactly the one where nothing came back to read a status from.
        """
        errors = pytest.importorskip("huggingface_hub.errors")

        def fake_init(self, model_name, **kwargs):
            raise errors.HfHubHTTPError("error sending request", response=None)

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        with pytest.raises(pytest.skip.Exception) as exc_info:
            _load_model("sentence-transformers/all-MiniLM-L6-v2")

        assert "network" in str(exc_info.value).lower()

    def test_a_truncated_download_still_skips(self, monkeypatch):
        """A mid-stream failure carries a 2xx status, and is still an outage.

        requests attaches the successful response to ChunkedEncodingError, so a
        classifier that treats "has a status" as "the server answered
        definitively" would fail the benchmark on a dropped connection. Only an
        ERROR status is definitive.
        """
        requests_exc = pytest.importorskip("requests.exceptions")

        def fake_init(self, model_name, **kwargs):
            raise requests_exc.ChunkedEncodingError(
                "connection broken: incomplete read", response=_response(200)
            )

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        with pytest.raises(pytest.skip.Exception) as exc_info:
            _load_model("sentence-transformers/all-MiniLM-L6-v2")

        assert "network" in str(exc_info.value).lower()

    def test_offline_with_no_cached_copy_still_skips(self, monkeypatch):
        """LocalEntryNotFoundError has no status and genuinely means unreachable.

        It subclasses EntryNotFoundError → HfHubHTTPError, so it would be caught
        by any status-based narrowing that assumed every HfHubHTTPError carries
        one. It does not, and offline-with-no-cache is a real outage.
        """
        errors = pytest.importorskip("huggingface_hub.errors")

        def fake_init(self, model_name, **kwargs):
            raise errors.LocalEntryNotFoundError(
                "Cannot reach the Hub and no cached copy exists"
            )

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        with pytest.raises(pytest.skip.Exception) as exc_info:
            _load_model("sentence-transformers/all-MiniLM-L6-v2")

        assert "network" in str(exc_info.value).lower()

    def test_a_broken_transitive_import_is_not_reported_as_missing(self, monkeypatch):
        """An ImportError from *inside* an installed module is not "not installed".

        If the splitter package is present but one of its own imports is missing
        or incompatible, that is a broken environment and must surface, not be
        rebranded as an absent optional dependency and skipped.
        """
        import importlib

        def boom(name):
            raise ModuleNotFoundError(
                "No module named 'some_unrelated_transitive_dep'",
                name="some_unrelated_transitive_dep",
            )

        monkeypatch.setattr(importlib, "import_module", boom)

        _assert_propagates(
            ModuleNotFoundError,
            lambda: _import_or_skip(
                "langchain_text_splitters.character", "CharacterTextSplitter"
            ),
            match="some_unrelated_transitive_dep",
        )

    def test_a_missing_benchmark_library_skips_instead_of_erroring(self, monkeypatch):
        """Every required benchmark import skips, not just the model one.

        The original test wrapped the model AND splitter imports in one
        ``except ImportError``. Moving only the model import into _load_model
        left the splitter and Document imports bare, so a missing dependency
        raised ModuleNotFoundError and errored the documented benchmark command
        instead of reporting a skip.
        """
        import importlib

        def boom(name):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)

        monkeypatch.setattr(importlib, "import_module", boom)

        with pytest.raises(pytest.skip.Exception) as exc_info:
            _import_or_skip(
                "langchain_text_splitters.character", "CharacterTextSplitter"
            )

        assert "langchain_text_splitters" in str(exc_info.value)
