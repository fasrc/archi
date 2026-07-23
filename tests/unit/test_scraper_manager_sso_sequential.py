"""SSO collection stays sequential (issue #136, task 6.1).

Spec ``parallel-scraping`` > "The selenium/SSO path remains sequential" >
scenario "SSO collection never uses the pool":

    the authenticator is never invoked from two threads at once, and the
    maximum observed concurrent use of the authenticator is 1

``_collect_sso_from_urls`` (``scraper_manager.py:425``) creates exactly one
selenium authenticator and drives it through a plain ``for url in urls`` loop, so
the peak observed concurrent use of that shared authenticator must be exactly 1.
If SSO collection were ever routed through ``run_seeds`` (the parallel pool),
multiple worker threads would call ``_handle_standard_url`` with the same shared
authenticator at once and the observed peak would exceed 1 -- this test locks in
that it does not.
"""

import threading
import time

import pytest

from src.data_manager.collectors.scrapers import scraper_manager as sm_module
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager


@pytest.fixture
def make_manager(tmp_path, monkeypatch):
    """Build a ScraperManager with a stubbed global config (DATA_PATH -> tmp_path)."""

    def _factory(dm_config=None):
        monkeypatch.setattr(
            sm_module,
            "get_global_config",
            lambda: {"DATA_PATH": str(tmp_path)},
        )
        return ScraperManager(dm_config)

    return _factory


class _FakeAuthenticator:
    """Stand-in selenium authenticator; only identity and ``close()`` matter."""

    def __init__(self, **kwargs):
        self.closed = 0

    def close(self):
        self.closed += 1


class TestSsoStaysSequential:
    def test_authenticator_never_used_concurrently(
        self, make_manager, monkeypatch, tmp_path
    ):
        manager = make_manager(
            {"sources": {"links": {"selenium_scraper": {"enabled": True}}}}
        )
        # SSO collection guards on the SSO secrets before doing any work.
        monkeypatch.setattr(sm_module, "read_secret", lambda name: "present")
        # One shared authenticator, created via the manager's resolve seam.
        monkeypatch.setattr(
            manager, "_resolve_scraper", lambda: (_FakeAuthenticator, {})
        )

        lock = threading.Lock()
        state = {"active": 0, "peak": 0}
        clients = []

        def fake_handle(
            url,
            persistence,
            output_dir,
            max_depth,
            client=None,
            use_client_for_scraping=False,
            scraper=None,
        ):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                clients.append(client)
            # Widen the use window so any real concurrency would overlap and be
            # observed as a peak > 1.
            time.sleep(0.02)
            with lock:
                state["active"] -= 1
            return 0

        monkeypatch.setattr(manager, "_handle_standard_url", fake_handle)

        urls = [f"https://sso{i}.example.edu/" for i in range(6)]
        manager._collect_sso_from_urls(urls, persistence=object(), output_dir=tmp_path)

        assert (
            state["peak"] == 1
        ), f"authenticator used from >1 thread at once (peak={state['peak']})"
        # Every seed was handled with the single shared authenticator instance.
        assert len(clients) == len(urls)
        assert len({id(c) for c in clients}) == 1
        assert isinstance(clients[0], _FakeAuthenticator)


class TestAuthenticatorLifecycle:
    """Authenticator closed exactly once per run (issue #136, task 6.2).

    Spec ``parallel-scraping`` > "The selenium/SSO path remains sequential" >
    scenario "Authenticator lifecycle is preserved":

        it is closed exactly once when the run finishes, including when a seed
        raises

    ``_collect_sso_from_urls`` (``scraper_manager.py``) builds one shared selenium
    authenticator and releases it in a ``try/finally`` so the browser session is
    always freed. These tests lock in that ``close()`` is invoked exactly once --
    neither leaked (never closed) nor double-closed -- whether every seed succeeds
    or one seed raises mid-loop.
    """

    def _make_sso_manager(self, make_manager, monkeypatch):
        """A selenium-enabled manager whose resolve seam yields one shared auth."""
        manager = make_manager(
            {"sources": {"links": {"selenium_scraper": {"enabled": True}}}}
        )
        monkeypatch.setattr(sm_module, "read_secret", lambda name: "present")
        authenticator = _FakeAuthenticator()
        # The manager builds the authenticator via ``authenticator_class(**kwargs)``;
        # return a factory that hands back the one instance we watch.
        monkeypatch.setattr(
            manager, "_resolve_scraper", lambda: (lambda **kw: authenticator, {})
        )
        return manager, authenticator

    def test_authenticator_closed_once_on_clean_run(
        self, make_manager, monkeypatch, tmp_path
    ):
        manager, authenticator = self._make_sso_manager(make_manager, monkeypatch)
        monkeypatch.setattr(manager, "_handle_standard_url", lambda *a, **k: 0)

        urls = [f"https://sso{i}.example.edu/" for i in range(4)]
        manager._collect_sso_from_urls(urls, persistence=object(), output_dir=tmp_path)

        assert authenticator.closed == 1

    def test_authenticator_closed_once_when_seed_raises(
        self, make_manager, monkeypatch, tmp_path
    ):
        manager, authenticator = self._make_sso_manager(make_manager, monkeypatch)

        def boom(url, *args, **kwargs):
            if url.endswith("sso1.example.edu/"):
                raise RuntimeError("seed blew up")
            return 0

        monkeypatch.setattr(manager, "_handle_standard_url", boom)

        urls = [f"https://sso{i}.example.edu/" for i in range(4)]
        with pytest.raises(RuntimeError, match="seed blew up"):
            manager._collect_sso_from_urls(
                urls, persistence=object(), output_dir=tmp_path
            )

        # The ``finally`` released the browser session exactly once despite the raise.
        assert authenticator.closed == 1


class TestSsoUntouchedByPool:
    """SSO collection is a plain sequential loop, not the pool (issue #136, task 6.3).

    Spec ``parallel-scraping`` > "The selenium/SSO path remains sequential":

        SSO collection and any selenium-backed standard collection SHALL continue
        to run one URL at a time against a single authenticator instance.

    Only the standard non-selenium link path (``_collect_links_from_urls``) is
    routed through ``run_seeds`` (the bounded worker pool). ``_collect_sso_from_urls``
    must remain a plain ``for url in urls`` loop that never dispatches through the
    pool. These tests lock in that ``run_seeds`` is never called during SSO
    collection and that seeds are handled strictly in input order.
    """

    def _make_sso_manager(self, make_manager, monkeypatch):
        manager = make_manager(
            {"sources": {"links": {"selenium_scraper": {"enabled": True}}}}
        )
        monkeypatch.setattr(sm_module, "read_secret", lambda name: "present")
        monkeypatch.setattr(
            manager, "_resolve_scraper", lambda: (_FakeAuthenticator, {})
        )
        return manager

    def test_run_seeds_is_never_called(self, make_manager, monkeypatch, tmp_path):
        manager = self._make_sso_manager(make_manager, monkeypatch)
        monkeypatch.setattr(manager, "_handle_standard_url", lambda *a, **k: 0)

        calls = []

        def spy_run_seeds(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("SSO collection must not route through run_seeds")

        monkeypatch.setattr(sm_module, "run_seeds", spy_run_seeds)

        urls = [f"https://sso{i}.example.edu/" for i in range(5)]
        manager._collect_sso_from_urls(urls, persistence=object(), output_dir=tmp_path)

        assert calls == []

    def test_seeds_handled_in_input_order(self, make_manager, monkeypatch, tmp_path):
        manager = self._make_sso_manager(make_manager, monkeypatch)

        order = []

        def record(url, *args, **kwargs):
            order.append(url)
            return 0

        monkeypatch.setattr(manager, "_handle_standard_url", record)

        urls = [f"https://sso{i}.example.edu/" for i in range(5)]
        manager._collect_sso_from_urls(urls, persistence=object(), output_dir=tmp_path)

        assert order == urls
