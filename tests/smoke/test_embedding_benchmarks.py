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

import pytest

# Anchored on OSError so ConnectionError, requests.ConnectionError, and
# huggingface_hub's LocalEntryNotFoundError (all OSError subclasses) are all
# covered. Extended with huggingface_hub's own offline/HTTP error types when
# that library is available. Never catches bare Exception — that would absorb
# AssertionError from a genuine embedding regression (see design D4).
_NETWORK_ERRORS: tuple[type[BaseException], ...] = (OSError,)
try:
    from huggingface_hub.errors import (
        HfHubHTTPError,
        LocalEntryNotFoundError,
        OfflineModeIsEnabled,
    )

    _NETWORK_ERRORS += (HfHubHTTPError, LocalEntryNotFoundError, OfflineModeIsEnabled)
except ImportError:
    pass


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

        from langchain_text_splitters.character import CharacterTextSplitter

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
        from langchain_core.documents import Document

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
        """Negative test (task 2.5): a wrong-dimension embedding fails, not skips.

        This test would catch a future widening of the guard to ``except
        Exception``, which would absorb AssertionError and make the benchmarks
        incapable of ever failing.
        """
        import time

        class FakeModel:
            def embed_documents(self, texts):
                # Return wrong dimension (128 instead of 384) to trigger assertion
                return [[0.0] * 128 for _ in texts]

        def fake_init(self, model_name, **kwargs):
            pass

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )
        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.embed_documents",
            FakeModel.embed_documents,
        )

        model = _load_model("sentence-transformers/all-MiniLM-L6-v2")
        test_texts = ["This is a test document.", "Another test."]

        start = time.time()
        embeddings = model.embed_documents(test_texts)
        elapsed = time.time() - start

        # This assertion should fail, not skip, proving the guard does not
        # swallow AssertionError.
        with pytest.raises(AssertionError):
            assert len(embeddings[0]) == 384, "Embedding dimension should be 384"
