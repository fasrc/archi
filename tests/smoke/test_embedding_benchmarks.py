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

from tests.support.embedding_guard import (
    _GUARDED_ERRORS,
    _NETWORK_ERROR_TYPES,
    _assert_propagates,
    _import_or_skip,
    _is_network_failure,
    _response,
)


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

    @pytest.mark.parametrize("status", [507, 520, 521, 522, 524, 530, 599])
    def test_any_server_side_status_is_an_outage(self, monkeypatch, status):
        """5xx is 5xx — enumerating four of them left the CDN's own codes out.

        HuggingFace sits behind Cloudflare, whose origin-trouble codes are
        520/521/522/524 — precisely the shape of the #187 incident. A fixed set
        of {500, 502, 503, 504} failed the deliberate benchmark on those, and
        contradicted the developer guide, which promises that a server-side 5xx
        is a skip. The rule is now "any 5xx", so the next code nobody enumerated
        is covered by construction rather than by another list entry.
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

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
    def test_a_definitive_client_status_is_not_an_outage(self, monkeypatch, status):
        """The 4xx boundary, pinned so widening 5xx cannot drift into 4xx."""
        errors = pytest.importorskip("huggingface_hub.errors")

        def fake_init(self, model_name, **kwargs):
            raise errors.HfHubHTTPError(
                f"{status} Client Error", response=_response(status)
            )

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        _assert_propagates(
            errors.HfHubHTTPError,
            lambda: _load_model("sentence-transformers/all-MiniLM-L6-v2"),
        )

    def test_a_malformed_endpoint_url_fails_instead_of_skipping(self, monkeypatch):
        """A bad HF_ENDPOINT is local misconfiguration, not an outage.

        requests.RequestException is the base of the whole requests hierarchy,
        including the URL-validation errors — InvalidURL, MissingSchema — that
        never touch the network at all. They carry no response, so the status
        check cannot filter them either, and naming the base class turned "you
        typed the endpoint wrong" into a green skip that says the network is
        unreachable.
        """
        requests_exc = pytest.importorskip("requests.exceptions")

        def fake_init(self, model_name, **kwargs):
            raise requests_exc.InvalidURL("Invalid URL 'htp://huggingface.co'")

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        _assert_propagates(
            requests_exc.InvalidURL,
            lambda: _load_model("sentence-transformers/all-MiniLM-L6-v2"),
        )

    def test_a_missing_url_scheme_fails_instead_of_skipping(self, monkeypatch):
        """MissingSchema is the same class of local defect as InvalidURL."""
        requests_exc = pytest.importorskip("requests.exceptions")

        def fake_init(self, model_name, **kwargs):
            raise requests_exc.MissingSchema("Invalid URL 'huggingface.co': No scheme")

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        _assert_propagates(
            requests_exc.MissingSchema,
            lambda: _load_model("sentence-transformers/all-MiniLM-L6-v2"),
        )

    def test_a_redirect_loop_fails_instead_of_skipping(self, monkeypatch):
        """A redirect loop is the server answering, repeatedly and wrongly.

        TooManyRedirects carries a 3xx response, so it slips under the >= 400
        status check as well — the type test is the only thing that can classify
        it, and the base class said "outage".
        """
        requests_exc = pytest.importorskip("requests.exceptions")

        def fake_init(self, model_name, **kwargs):
            raise requests_exc.TooManyRedirects(
                "Exceeded 30 redirects", response=_response(302)
            )

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        _assert_propagates(
            requests_exc.TooManyRedirects,
            lambda: _load_model("sentence-transformers/all-MiniLM-L6-v2"),
        )

    @pytest.mark.parametrize(
        "exc_name",
        ["ConnectionError", "Timeout", "ReadTimeout", "SSLError", "RetryError"],
    )
    def test_a_requests_transport_failure_still_skips(self, monkeypatch, exc_name):
        """The transport families must survive dropping the base class.

        RequestException was originally named for a good reason:
        requests.ConnectionError is NOT a builtin ConnectionError (its MRO is
        RequestException → OSError) and its errno is None, so neither the builtin
        type nor the errno table catches it. Narrowing to the transport families
        must not lose that — a real CDN failure has to keep skipping.
        """
        requests_exc = pytest.importorskip("requests.exceptions")
        exc_type = getattr(requests_exc, exc_name)

        def fake_init(self, model_name, **kwargs):
            raise exc_type("transport failure reaching the CDN")

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        with pytest.raises(pytest.skip.Exception) as exc_info:
            _load_model("sentence-transformers/all-MiniLM-L6-v2")

        assert "network" in str(exc_info.value).lower()

    @pytest.mark.parametrize("exc_name", ["UnsupportedProtocol", "LocalProtocolError"])
    def test_an_httpx_client_side_error_fails_instead_of_skipping(
        self, monkeypatch, exc_name
    ):
        """httpx.TransportError has the same problem RequestException had.

        UnsupportedProtocol (`HF_ENDPOINT=htp://…`) and LocalProtocolError (a
        header WE sent is illegal) are both subclasses of TransportError, and
        neither carries a >= 400 response, so naming the base turned local
        misconfiguration into a green skip. Same defect as the requests side, one
        line below it in the same tuple.
        """
        httpx = pytest.importorskip("httpx")
        exc_type = getattr(httpx, exc_name)

        def fake_init(self, model_name, **kwargs):
            raise exc_type("client-side protocol problem")

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        _assert_propagates(
            exc_type, lambda: _load_model("sentence-transformers/all-MiniLM-L6-v2")
        )

    @pytest.mark.parametrize(
        "exc_name",
        [
            "ConnectError",
            "ReadError",
            "WriteError",
            "CloseError",
            "ReadTimeout",
            "PoolTimeout",
            "ProxyError",
            "RemoteProtocolError",
        ],
    )
    def test_an_httpx_transport_family_still_skips(self, monkeypatch, exc_name):
        """The httpx transport families must survive dropping their base class.

        RemoteProtocolError is deliberately in this list while its sibling
        LocalProtocolError is not: there the SERVER broke the protocol
        mid-transfer, which is an outage, whereas a local protocol error is our
        own malformed request.
        """
        httpx = pytest.importorskip("httpx")
        exc_type = getattr(httpx, exc_name)

        def fake_init(self, model_name, **kwargs):
            raise exc_type("transport failure reaching the CDN")

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        with pytest.raises(pytest.skip.Exception) as exc_info:
            _load_model("sentence-transformers/all-MiniLM-L6-v2")

        assert "network" in str(exc_info.value).lower()

    @pytest.mark.parametrize(
        "exc_name",
        [
            "RepositoryNotFoundError",
            "GatedRepoError",
            "DisabledRepoError",
            "RevisionNotFoundError",
            "EntryNotFoundError",
        ],
    )
    def test_a_definitive_hub_error_without_a_response_still_fails(
        self, monkeypatch, exc_name
    ):
        """The status check cannot save us when there is no status to read.

        These six subclasses of HfHubHTTPError all name a definitive condition,
        and they were reaching the skip through their base class rather than
        through their status. Normally hf_raise_for_status attaches a response
        and the status check classifies them, but naming the base meant the
        classification depended on that attachment. It no longer does: the base
        is matched by EXACT type, so only a bare HfHubHTTPError with no response
        is read as "nothing came back".
        """
        errors = pytest.importorskip("huggingface_hub.errors")
        exc_type = getattr(errors, exc_name, None)
        if exc_type is None:
            pytest.skip(f"this huggingface_hub has no {exc_name}")

        def fake_init(self, model_name, **kwargs):
            raise exc_type("definitive answer, no response attached")

        monkeypatch.setattr(
            "langchain_huggingface.HuggingFaceEmbeddings.__init__", fake_init
        )

        _assert_propagates(
            exc_type, lambda: _load_model("sentence-transformers/all-MiniLM-L6-v2")
        )

    def test_no_named_network_type_drags_in_a_local_defect(self):
        """Guard the CLASS of defect, not its instances.

        Three consecutive review rounds each found one over-broad base class in
        `_NETWORK_ERROR_TYPES` — `HfHubHTTPError`, then
        `requests.RequestException`, then `httpx.TransportError` — and each time
        the fix was to name the transport families instead. Fixing them one at a
        time is what produced three rounds.

        This test enumerates the client-side and definitive error types the guard
        must never absorb and asserts that none of them is a subclass of anything
        in the allowlist. Adding a convenient base class back fails here, with
        the offending pair named, instead of surfacing as a false green skip in a
        run nobody is watching.
        """
        local_defects: list[type[BaseException]] = []
        try:
            from requests import exceptions as requests_exc

            local_defects += [
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

            local_defects += [httpx.UnsupportedProtocol, httpx.LocalProtocolError]
        except ImportError:
            pass
        try:
            from huggingface_hub import errors as hub_errors

            local_defects += [
                hub_errors.RepositoryNotFoundError,
                hub_errors.GatedRepoError,
                hub_errors.DisabledRepoError,
                hub_errors.RevisionNotFoundError,
                hub_errors.BadRequestError,
            ]
        except ImportError:
            pass

        if not local_defects:
            pytest.skip("no optional HTTP library installed to audit")

        for defect in local_defects:
            for named in _NETWORK_ERROR_TYPES:
                assert not issubclass(defect, named), (
                    f"{defect.__module__}.{defect.__name__} is a subclass of the "
                    f"allowlisted {named.__module__}.{named.__name__}, so a local "
                    "or definitive failure would be reported as a network outage. "
                    "Name the transport families individually instead of their "
                    "shared base."
                )

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
