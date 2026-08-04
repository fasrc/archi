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

import importlib
import socket

import pytest

# The transport failures that mean "the CDN was unreachable" — enumerated by
# family, NOT anchored on the whole OSError hierarchy. OSError would also absorb
# PermissionError on the model cache, ENOSPC on a full disk, and an unreadable
# or corrupt cache directory, reporting each as a network skip and hiding
# precisely the local benchmark defects _load_model promises to propagate.
# Never catches bare Exception either — that would absorb AssertionError from a
# genuine embedding regression (see design D4).
#
# Each optional family is appended only when its library is importable, so the
# tuple stays valid in a minimal environment:
#   - requests.RequestException covers requests.ConnectionError/Timeout, and
#     huggingface_hub's HfHubHTTPError, which subclasses it.
#   - httpx.TransportError covers ConnectError/TimeoutException/ReadError; none
#     of the httpx errors are OSError subclasses, so they need naming here.
_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    socket.gaierror,
)
try:
    from requests.exceptions import RequestException

    _NETWORK_ERRORS += (RequestException,)
except ImportError:
    pass
try:
    from huggingface_hub.errors import (
        HfHubHTTPError,
        LocalEntryNotFoundError,
        OfflineModeIsEnabled,
    )

    _NETWORK_ERRORS += (HfHubHTTPError, LocalEntryNotFoundError, OfflineModeIsEnabled)
except ImportError:
    pass
try:
    from httpx import TransportError

    _NETWORK_ERRORS += (TransportError,)
except ImportError:
    pass


def _import_or_skip(module: str, attr: str):
    """Import *attr* from *module*, or skip naming the missing library.

    Mirrors _load_model's missing-library contract for the benchmark's other
    imports. The original test wrapped the model and splitter imports in one
    ``except ImportError``; when the model import moved into _load_model, the
    splitter and Document imports were left bare, so a missing dependency
    raised ModuleNotFoundError and errored the documented benchmark command
    instead of reporting a skip.
    """
    try:
        loaded = importlib.import_module(module)
    except ImportError:
        pytest.skip(f"{module} not installed")
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
    except _NETWORK_ERRORS as exc:
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

        # Deliberately not `pytest.raises`: if the guard were widened, the
        # AssertionError would be converted to a skip, and a bare
        # `pytest.raises` block would let that skip propagate — reporting this
        # test as SKIPPED, which reads as green in CI. Catching the skip and
        # calling pytest.fail makes the regression loud instead of invisible.
        try:
            _load_model("sentence-transformers/all-MiniLM-L6-v2")
        except AssertionError as exc:
            assert "embedding regression" in str(exc)
        except pytest.skip.Exception as exc:
            pytest.fail(f"the guard swallowed AssertionError into a skip: {exc}")
        else:
            pytest.fail("_load_model neither raised nor skipped")

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

        with pytest.raises(PermissionError):
            _load_model("sentence-transformers/all-MiniLM-L6-v2")

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
            raise ModuleNotFoundError(f"No module named {name!r}")

        monkeypatch.setattr(importlib, "import_module", boom)

        with pytest.raises(pytest.skip.Exception) as exc_info:
            _import_or_skip(
                "langchain_text_splitters.character", "CharacterTextSplitter"
            )

        assert "langchain_text_splitters" in str(exc_info.value)
