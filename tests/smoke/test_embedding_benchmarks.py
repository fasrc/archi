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

# What _load_model actually catches: a named, bounded set. NEVER bare Exception —
# that would absorb an AssertionError from a real embedding regression and make
# the benchmark incapable of failing (see design D4, and the spec requirement
# "The network guard names specific exception types"). OSError is in the set so
# the plain-OSError errnos above are reachable; it is then classified by errno,
# so PermissionError on the model cache, ENOSPC on a full disk and a corrupt
# cache directory still propagate as the local defects they are.
_GUARDED_ERRORS: tuple[type[BaseException], ...] = _NETWORK_ERROR_TYPES + (OSError,)


def _is_network_failure(exc: BaseException) -> bool:
    """True when *exc* means the weights could not be fetched over the network."""
    if isinstance(exc, _NETWORK_ERROR_TYPES):
        return True
    return isinstance(exc, OSError) and exc.errno in _NETWORK_ERRNOS


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


class TestEmbeddingGuard:
    """Guard and negative tests for the network-skip helper.

    These run without a network connection — they monkeypatch the constructor
    so the CDN is never reached. Their purpose is to keep the guard honest
    across refactors: task 2.3 (red) proves the guard works; task 2.5
    (negative) proves it does not swallow AssertionError.
    """

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
