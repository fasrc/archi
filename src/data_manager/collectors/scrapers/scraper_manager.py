from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple

from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.scrapers.scrape_pool import (
    host_key,
    run_seeds,
    shared_host_limiter,
)
from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource
from src.data_manager.collectors.scrapers.scraper import LinkScraper
from src.data_manager.collectors.scrapers.sitemap_source import (
    SitemapExpansionError,
    normalize_page_url,
)
from src.utils.config_access import get_global_config
from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)


# ``int()`` rejects a non-finite float with ``OverflowError``, which is NOT a
# subclass of ``ValueError`` — so YAML's `.inf` (a perfectly ordinary way to write
# "no limit") escapes a bare (TypeError, ValueError) guard and takes down whatever
# is being constructed. Every tolerant coercion in this module promises to fall back
# on bad input, so all of them catch this triple.
_COERCION_ERRORS = (TypeError, ValueError, OverflowError)


def _parse_worker_knob(value: Any, name: str, default: int) -> int:
    """Coerce a scrape-concurrency knob to an int, tolerating junk.

    Falls back to ``default`` with a logged warning when ``value`` is unset or not a
    valid integer, then clamps the result to a minimum of 1.
    """
    if value is None:
        resolved = default
    else:
        try:
            resolved = int(value)
        except _COERCION_ERRORS:
            logger.warning(
                "Invalid '%s' value %r. Falling back to default %d.",
                name,
                value,
                default,
            )
            resolved = default
    return max(1, resolved)


def _dedup_key(u: str) -> str:
    """Normalize a URL for deduplication; fall back to the raw string on ValueError."""
    try:
        return normalize_page_url(u)
    except ValueError:
        return u


if TYPE_CHECKING:
    from src.data_manager.collectors.scrapers.integrations.git_scraper import GitScraper
    from src.data_manager.collectors.scrapers.integrations.indico_scraper import (
        IndicoScraper,
    )


class ScraperManager:
    """Coordinates scraper integrations and centralises persistence logic."""

    def __init__(self, dm_config: Optional[Dict[str, Any]] = None) -> None:
        global_config = get_global_config()

        sources_config = (dm_config or {}).get("sources", {}) or {}
        links_config = (
            sources_config.get("links", {}) if isinstance(sources_config, dict) else {}
        )
        selenium_config = (
            links_config.get("selenium_scraper", {})
            if isinstance(sources_config, dict)
            else {}
        )

        git_config = (
            sources_config.get("git", {}) if isinstance(sources_config, dict) else {}
        )
        sso_config = (
            sources_config.get("sso", {}) if isinstance(sources_config, dict) else {}
        )
        indico_config = (
            sources_config.get("indico", {}) if isinstance(sources_config, dict) else {}
        )
        self.base_depth = links_config.get("base_source_depth", 5)
        logger.debug(f"Using base depth of {self.base_depth} for weblist URLs")

        scraper_config = {}
        if isinstance(links_config, dict):
            scraper_config = links_config.get("html_scraper", {}) or {}
        self.config = scraper_config
        self.sitemap_config = (
            links_config.get("sitemap", {}) if isinstance(links_config, dict) else {}
        )
        raw_max_pages = links_config.get("max_pages")
        self.max_pages = None
        if raw_max_pages not in (None, ""):
            try:
                self.max_pages = int(raw_max_pages)
            except _COERCION_ERRORS:
                logger.warning(f"Invalid max_pages value {raw_max_pages}; ignoring.")

        # Scrape-phase concurrency knobs (issue #136). Independent of the embedding
        # phase's `parallel_workers`. Tolerant parse mirrors VectorStoreManager: coerce
        # to int, fall back to the default with a logged warning on junk, clamp to >= 1.
        dm = dm_config or {}
        self.scrape_workers = _parse_worker_knob(
            dm.get("scrape_workers"), "scrape_workers", 8
        )
        self.scrape_per_host_workers = _parse_worker_knob(
            dm.get("scrape_per_host_workers"), "scrape_per_host_workers", 4
        )

        self.links_enabled = True
        self.git_enabled = (
            git_config.get("enabled", False) if isinstance(git_config, dict) else True
        )
        self.git_config = git_config if isinstance(git_config, dict) else {}
        self.indico_enabled = (
            indico_config.get("enabled", False)
            if isinstance(indico_config, dict)
            else False
        )
        self.indico_config = indico_config if isinstance(indico_config, dict) else {}
        self.selenium_config = selenium_config or {}
        self.selenium_enabled = self.selenium_config.get("enabled", False)
        self.scrape_with_selenium = self.selenium_config.get("use_for_scraping", False)

        self.sso_enabled = bool(sso_config.get("enabled", False))

        elog_config = (
            sources_config.get("elog", {}) if isinstance(sources_config, dict) else {}
        )
        self.elog_config = elog_config if isinstance(elog_config, dict) else {}
        # Gate on the explicit `enabled` flag (like git/indico/jira/redmine), not just
        # URL presence, so disabling ELOG while leaving the URL set stops collection.
        self.elog_enabled = bool(self.elog_config.get("enabled", False)) and bool(
            self.elog_config.get("url")
        )

        self.data_path = Path(global_config["DATA_PATH"])
        self.input_lists = links_config.get("input_lists", [])
        self.git_dir = self.data_path / "git"

        self.data_path.mkdir(parents=True, exist_ok=True)

        # Shared scraper for the sequential and selenium/SSO paths. The parallel
        # link path must NOT reuse this instance: crawl_iter resets and mutates
        # per-instance state, so concurrent seeds each need their own scraper
        # from _new_link_scraper() (issue #136).
        self.web_scraper = self._new_link_scraper()
        self._git_scraper: Optional["GitScraper"] = None
        self._indico_scraper: Optional["IndicoScraper"] = None

    def _new_link_scraper(self) -> LinkScraper:
        """Build a fresh LinkScraper for a single seed crawl.

        Each concurrent crawl in the parallel link path gets its own instance:
        crawl_iter resets and mutates per-instance ``visited_urls`` /
        ``seen_urls`` / ``page_data`` for the duration of a crawl, so sharing one
        instance across threads would let one seed's reset corrupt another's
        in-flight state (issue #136). The construction args mirror the shared
        sequential ``self.web_scraper`` so per-worker scrapers behave identically.
        """
        return LinkScraper(
            verify_urls=self.config.get(
                "verify_urls", False
            ),  # Default to False for broader compatibility
            enable_warnings=self.config.get("enable_warnings", False),
        )

    def collect_all_from_config(self, persistence: PersistenceService) -> None:
        """Run the configured scrapers and persist their output."""
        link_urls, git_urls, sso_urls, elog_urls, indico_urls, sitemap_urls = (
            self._collect_urls_from_lists_by_type(self.input_lists)
        )

        if git_urls:
            self.git_enabled = True
        if sso_urls:
            self.sso_enabled = True
            self._ensure_sso_defaults()

        # Expand any `sitemap-` sources into page URLs and append (dedup,
        # order-preserving) before standard link collection. A below-floor /
        # over-cap SitemapExpansionError is intentionally NOT caught here: it
        # propagates out and fails the ingest rather than shipping a bad corpus.
        self._sitemap_lastmod_map: Dict[str, str] = {}
        if sitemap_urls:
            # Dedup expanded pages against the NORMALIZED hand-list keys, not the
            # raw strings, so a hand-listed `/x/` and a sitemap-derived `/x` are the
            # same page. LinkScraper does not dedup across seeds, so without this a
            # slash/case/fragment variant would be scraped twice (#118) during the
            # hand-list -> sitemap migration window. Expanded URLs are already
            # normalized, so they compare directly against these keys.
            existing_keys = {_dedup_key(u) for u in link_urls}
            link_urls.extend(
                self._refresh_sitemap_lastmod_map(sitemap_urls, existing_keys)
            )

        self.collect_links(persistence, link_urls=link_urls)
        self.collect_sso(persistence, sso_urls=sso_urls)
        self.collect_git(persistence, git_urls=git_urls)
        self.collect_elog(persistence, extra_urls=elog_urls)
        self.collect_indico(persistence, indico_urls=indico_urls)

        logger.info("Web scraping was completed successfully")

    def collect_links(
        self,
        persistence: PersistenceService,
        link_urls: List[str] = [],
        max_depth: Optional[int] = None,
    ) -> int:
        """Collect only standard link sources. Returns count of resources scraped."""
        if not self.links_enabled:
            logger.info("Links disabled, skipping link scraping")
            return 0
        if not link_urls:
            return 0
        websites_dir = persistence.data_path / "websites"
        if not os.path.exists(websites_dir):
            os.makedirs(websites_dir, exist_ok=True)
        return self._collect_links_from_urls(
            link_urls, persistence, websites_dir, max_depth=max_depth
        )

    def collect_git(
        self,
        persistence: PersistenceService,
        git_urls: Optional[List[str]] = None,
    ) -> None:
        """Collect only git sources."""
        if not self.git_enabled:
            logger.info("Git disabled, skipping git scraping")
            return
        if not git_urls:
            return
        git_dir = persistence.data_path / "git"
        if not os.path.exists(git_dir):
            os.makedirs(git_dir, exist_ok=True)
        self._collect_git_resources(git_urls, persistence, git_dir)

    def collect_indico(
        self,
        persistence: PersistenceService,
        indico_urls: Optional[List[str]] = None,
    ) -> None:
        """Collect Indico events and materials."""
        if not self.indico_enabled:
            logger.info("Indico disabled, skipping Indico scraping")
            return
        if not indico_urls:
            return
        indico_dir = persistence.data_path / "indico"
        if not os.path.exists(indico_dir):
            os.makedirs(indico_dir, exist_ok=True)
        self._collect_indico_resources(indico_urls, persistence, indico_dir)

    def collect_sso(
        self,
        persistence: PersistenceService,
        sso_urls: Optional[List[str]] = None,
    ) -> None:
        """Collect only SSO sources."""
        if not self.sso_enabled:
            logger.info("SSO disabled, skipping SSO scraping")
            return
        self._ensure_sso_defaults()
        if not sso_urls:
            return
        sso_dir = persistence.data_path / "sso"
        if not os.path.exists(sso_dir):
            os.makedirs(sso_dir, exist_ok=True)
        self._collect_sso_from_urls(sso_urls, persistence, sso_dir)

    def schedule_collect_links(
        self, persistence: PersistenceService, last_run: Optional[str] = None
    ) -> None:
        """
        Scheduled collection of link sources.
        For now, this behaves the same as a full collection, overriding last_run depending on the persistence layer.
        """
        metadata = persistence.catalog.get_metadata_by_filter(
            "source_type", source_type="web", metadata_keys=["url"]
        )
        catalog_urls = [m[1].get("url", "").strip() for m in metadata]
        catalog_urls = [u for u in catalog_urls if u]
        logger.info(
            "Scheduled links collection found %d URL(s) in catalog", len(catalog_urls)
        )
        link_urls, _, _, _, _, sitemap_urls = self._collect_urls_from_lists_by_type(
            self.input_lists
        )
        if sitemap_urls:
            existing_keys = {_dedup_key(u) for u in link_urls}
            try:
                self._refresh_sitemap_lastmod_map(sitemap_urls, existing_keys)
            except SitemapExpansionError as exc:
                # Degrading past this error is only safe with a map already in hand.
                # service_data_manager keeps running after its asynchronous initial
                # ingest reports an error, so this pass can arrive with no map at all
                # while the catalog still holds rows from an earlier process. Crawling
                # then stamps nothing, and the upsert's unconditional
                # `last_modified = EXCLUDED.last_modified` writes NULL over every one
                # of those rows. Skipping one scheduled refresh is recoverable; losing
                # the timestamps is not.
                if not getattr(self, "_sitemap_lastmod_map", None):
                    logger.error(
                        "sitemap expansion failed with no previous lastmod map (%s); "
                        "skipping this scheduled crawl rather than overwriting stored "
                        "last_modified values with NULL",
                        exc,
                    )
                    return
                logger.warning(str(exc))
        else:
            # Every sitemap source has been removed or reclassified. Wholesale
            # replacement is the semantics used when an individual page disappears,
            # so a source disappearing must clear the map too — otherwise the crawl
            # keeps stamping pages from a sitemap that is no longer configured.
            self._sitemap_lastmod_map = {}
        self.collect_links(persistence, link_urls=catalog_urls)

    def schedule_collect_git(
        self, persistence: PersistenceService, last_run: Optional[str] = None
    ) -> None:
        metadata = persistence.catalog.get_metadata_by_filter(
            "source_type", source_type="git", metadata_keys=["url"]
        )
        catalog_urls = [m[1].get("url", "") for m in metadata]
        self.collect_git(persistence, git_urls=catalog_urls)

    def schedule_collect_indico(
        self, persistence: PersistenceService, last_run: Optional[str] = None
    ) -> None:
        """Scheduled collection of Indico sources.

        Indico documents share source_type="web" with link/elog scrapers, so we
        match on the metadata-level "scraper" field instead.
        """
        metadata = persistence.catalog.get_metadata_by_filter(
            "scraper", scraper="indico", metadata_keys=["url"]
        )
        catalog_urls = [m[1].get("url", "") for m in metadata]
        self.collect_indico(persistence, indico_urls=catalog_urls)

    def schedule_collect_sso(
        self, persistence: PersistenceService, last_run: Optional[str] = None
    ) -> None:
        metadata = persistence.catalog.get_metadata_by_filter(
            "source_type", source_type="sso", metadata_keys=["url"]
        )
        catalog_urls = [m[1].get("url", "") for m in metadata]
        self.collect_sso(persistence, sso_urls=catalog_urls)

    def schedule_collect_elog(
        self, persistence: PersistenceService, last_run: Optional[str] = None
    ) -> None:
        # ELOG entries are stored with source_type="web", so match the metadata-level
        # "scraper" marker instead (mirrors schedule_collect_indico).
        metadata = persistence.catalog.get_metadata_by_filter(
            "scraper", scraper="elog", metadata_keys=["url"]
        )
        catalog_urls = [m[1].get("url", "") for m in metadata]
        self.collect_elog(persistence, extra_urls=catalog_urls)

    def collect_elog(
        self, persistence: PersistenceService, extra_urls: Optional[List[str]] = None
    ) -> int:
        """Collect all entries from configured ELOG logbooks.

        Sources:
          - dedicated  ``elog:`` config section (url key)
          - URLs auto-detected as ELOG from input_lists (passed via extra_urls)
        """
        from src.data_manager.collectors.scrapers.integrations.elog_scraper import (
            ElogScraper,
        )

        elog_dir = persistence.data_path / "websites"
        elog_dir.mkdir(parents=True, exist_ok=True)

        urls_to_scrape: List[str] = list(extra_urls) if extra_urls else []
        if self.elog_enabled:
            urls_to_scrape.append(self.elog_config.get("url"))

        # Normalize and deduplicate URLs while preserving order
        normalized_urls: List[str] = []
        seen = set()
        for raw_url in urls_to_scrape:
            if not raw_url:
                continue
            url = raw_url.rstrip("/")
            if url and url not in seen:
                seen.add(url)
                normalized_urls.append(url)
        urls_to_scrape = normalized_urls

        if not urls_to_scrape:
            return 0

        total = 0
        for url in urls_to_scrape:
            cfg = {**self.elog_config, "url": url}
            scraper = ElogScraper(cfg)
            for resource in scraper.iter_entries():
                persistence.persist_resource(resource, elog_dir)
                total += 1
        logger.info(f"ELOG scraping complete: {total} entries collected")
        return total

    def _collect_links_from_urls(
        self,
        urls: List[str],
        persistence: PersistenceService,
        output_dir: Path,
        max_depth: Optional[int] = None,
    ) -> int:
        """Collect links from URLs and return total count of resources scraped."""
        # Initialize authenticator if selenium is enabled
        authenticator = None
        if self.selenium_enabled:
            authenticator_class, kwargs = self._resolve_scraper()
            if authenticator_class is not None:
                authenticator = authenticator_class(**kwargs)

        depth = max_depth if max_depth is not None else self.base_depth

        # One limiter object, used for two things: bounding the pool, and letting a
        # crawl move its slot when a redirect lands it on a different host than the
        # seed it was dispatched for (issue #136 review). It is the process-wide
        # limiter, so an overlapping upload_url batch contends with this one.
        limiter = shared_host_limiter(self.scrape_per_host_workers)

        def _scrape_one_seed(seed: str) -> int:
            # For standard link collection, don't use selenium for scraping (SSO
            # urls are handled separately via collect_sso). Each concurrent seed
            # crawl gets its own LinkScraper from the factory seam so that the
            # per-instance state crawl_iter resets is never shared across threads
            # (issue #136).
            return self._handle_standard_url(
                seed,
                persistence,
                output_dir,
                max_depth=depth,
                client=None,
                use_client_for_scraping=False,
                scraper=self._new_link_scraper(),
                on_request_url=lambda url: limiter.rekey_current(host_key(url)),
            )

        try:
            total_count = run_seeds(
                urls,
                _scrape_one_seed,
                workers=self.scrape_workers,
                per_host_workers=self.scrape_per_host_workers,
                limiter=limiter,
            )
        finally:
            if authenticator is not None:
                authenticator.close()  # Close the authenticator properly and free the resources
        return total_count

    def _collect_sso_from_urls(
        self,
        urls: List[str],
        persistence: PersistenceService,
        output_dir: Path,
    ) -> None:
        """Collect SSO-protected URLs using selenium for authentication."""
        if not self.selenium_enabled:
            logger.error(
                "SSO scraping requires data_manager.sources.links.selenium_scraper.enabled"
            )
            return
        if not read_secret("SSO_USERNAME") or not read_secret("SSO_PASSWORD"):
            logger.error("SSO scraping requires SSO_USERNAME and SSO_PASSWORD secrets")
            return
        authenticator = None
        if self.selenium_enabled:
            authenticator_class, kwargs = self._resolve_scraper()
            if authenticator_class is not None:
                authenticator = authenticator_class(**kwargs)

        if authenticator is None:
            logger.error(
                "SSO collection requires a valid selenium scraper configuration"
            )
            return

        try:
            for url in urls:
                # For SSO URLs, use selenium client for authentication
                # scrape_with_selenium determines if we use selenium for scraping too
                self._handle_standard_url(
                    url,
                    persistence,
                    output_dir,
                    max_depth=self.base_depth,
                    client=authenticator,
                    use_client_for_scraping=self.scrape_with_selenium,
                )
        finally:
            if authenticator is not None:
                authenticator.close()

    def _ensure_sso_defaults(self) -> None:
        if not self.selenium_config:
            self.selenium_config = {}

        if not self.selenium_enabled:
            self.selenium_config["enabled"] = True
            self.selenium_enabled = True

        if not self.selenium_config.get("selenium_class"):
            self.selenium_config["selenium_class"] = "CERNSSOScraper"

        class_map = self.selenium_config.setdefault("selenium_class_map", {})
        if "CERNSSOScraper" not in class_map:
            class_map["CERNSSOScraper"] = {
                "class": "CERNSSOScraper",
                "kwargs": {
                    "headless": True,
                    "max_depth": 2,
                },
            }

    def _collect_urls_from_lists(self, input_lists) -> List[str]:
        """Collect URLs from the configured weblists."""
        # Handle case where input_lists might be None
        urls: List[str] = []
        if not input_lists:
            return urls
        for list_name in input_lists:
            list_path = Path("weblists") / Path(list_name).name
            if not list_path.exists():
                logger.warning(f"Input list {list_path} not found.")
                continue

            urls.extend(self._extract_urls_from_file(list_path))

        return urls

    def _collect_urls_from_lists_by_type(
        self, input_lists: List[str]
    ) -> tuple[List[str], List[str], List[str], List[str], List[str], List[str]]:
        """All types of URLs are in the same input lists, separate them via prefixes or auto-detection."""
        link_urls: List[str] = []
        git_urls: List[str] = []
        sso_urls: List[str] = []
        elog_urls: List[str] = []
        indico_urls: List[str] = []
        sitemap_urls: List[str] = []
        for raw_url in self._collect_urls_from_lists(input_lists):
            if raw_url.startswith("git-"):
                git_urls.append(raw_url.split("git-", 1)[1])
                continue
            if raw_url.startswith("sso-"):
                sso_urls.append(raw_url.split("sso-", 1)[1])
                continue
            # Explicit `sitemap-` prefix is peeled before the elog/indico
            # auto-detection heuristics below, so a sitemap URL whose path
            # happens to contain `/elog/` or `/event/` still routes to sitemap
            # expansion (mirrors the explicit-prefix-beats-heuristic rule).
            if raw_url.startswith("sitemap-"):
                sitemap_urls.append(raw_url.split("sitemap-", 1)[1])
                continue
            if raw_url.startswith("elog-"):
                elog_urls.append(raw_url.split("elog-", 1)[1])
                continue
            if self._is_elog_url(raw_url):
                elog_urls.append(raw_url)
                continue
            if raw_url.startswith("indico-"):
                indico_urls.append(raw_url.split("indico-", 1)[1])
                continue
            if self._is_indico_url(raw_url):
                indico_urls.append(raw_url)
                continue
            link_urls.append(raw_url)
        return link_urls, git_urls, sso_urls, elog_urls, indico_urls, sitemap_urls

    def _expand_sitemaps(
        self, sitemap_urls: List[str]
    ) -> List[Tuple[str, Optional[str]]]:
        """Expand ``sitemap-`` source URLs into (page_url, lastmod|None) pairs.

        Thin call site over :mod:`sitemap_source`: builds the trust/bounds policy
        from the ``sources.links.sitemap`` config sub-block plus a ``requests``
        fetch, then delegates. A source-level ``SitemapExpansionError``
        (below-floor / over-cap) is allowed to propagate so it FAILS the ingest
        rather than shipping an empty or runaway corpus.
        """
        from functools import partial

        from src.data_manager.collectors.scrapers import sitemap_source

        def _as_int(value, default: int) -> int:
            try:
                return int(value)
            except _COERCION_ERRORS:
                return default

        cfg = self.sitemap_config if isinstance(self.sitemap_config, dict) else {}
        raw_hosts = cfg.get("allowed_hosts", []) or []
        # A YAML scalar (`allowed_hosts: cdn.example.com`) must be treated as a
        # single host, not char-exploded by list("host").
        if isinstance(raw_hosts, str):
            raw_hosts = [raw_hosts]
        policy = sitemap_source.SitemapPolicy(
            allowed_hosts=[str(host) for host in raw_hosts],
            min_pages=_as_int(cfg.get("min_pages"), 1),
            max_pages=_as_int(cfg.get("max_pages"), 20000),
        )
        fetch = partial(
            sitemap_source.fetch_sitemap_text,
            verify=self.config.get("verify_urls", False),
        )
        # Record per-document failures so the caller can tell a COMPLETE expansion
        # from one that merely did not raise. Expansion fails open per document, so
        # without this a failed child of a <sitemapindex> is indistinguishable from
        # a sitemap that genuinely lists fewer pages — and republishing the map from
        # that truncated result silently drops the failed child's timestamps.
        failures: List[Tuple[str, Exception]] = []
        pairs = list(
            sitemap_source.expand_sitemaps(
                sitemap_urls,
                fetch,
                policy,
                lambda url, exc: failures.append((url, exc)),
            )
        )
        self._sitemap_expansion_failures = failures
        self._sitemap_expansion_incomplete = bool(failures)
        return pairs

    def _refresh_sitemap_lastmod_map(
        self, sitemap_urls: List[str], existing_keys: Set[str]
    ) -> List[str]:
        """Expand sitemaps, rebuild ``_sitemap_lastmod_map``, return new page URLs.

        Walks the expanded pairs in order, skipping URLs already in
        ``existing_keys`` (mutating that set as it goes so the caller's dedup
        state stays correct).  Builds the map into a local dict and publishes it
        only when the expansion was COMPLETE.

        Completeness is not the same as "did not raise". ``SitemapExpansionError``
        covers only source-level bounds (over cap, below floor); a per-document
        fetch/parse failure fails open, contributing zero pairs with a WARNING
        (``sitemap_source._fetch_and_parse``). So a ``<sitemapindex>`` whose child
        fails while its siblings still clear ``min_pages`` yields a TRUNCATED list
        and no exception. Publishing that would discard every timestamp belonging
        to the failed child, and the catalog crawl would then conflict-upsert NULL
        over those rows. When the expansion was incomplete and a usable map already
        exists, the previous map is retained instead (design D3, now actually
        enforced rather than merely asserted).

        Replacement stays wholesale whenever the expansion WAS complete, so a page
        genuinely removed from the sitemap still drops out.

        ``SitemapExpansionError`` always propagates; the caller decides whether
        to catch it (design D1).
        """
        sitemap_pairs = self._expand_sitemaps(sitemap_urls)
        new_map: Dict[str, str] = {}
        new_urls: List[str] = []
        for url, lastmod in sitemap_pairs:
            if url not in existing_keys:
                existing_keys.add(url)
                new_urls.append(url)
                if lastmod is not None:
                    new_map[url] = lastmod
        incomplete = getattr(self, "_sitemap_expansion_incomplete", False)
        previous = getattr(self, "_sitemap_lastmod_map", None)
        if incomplete and previous:
            logger.warning(
                "sitemap expansion was incomplete (%d document(s) failed to fetch or "
                "parse); retaining the previous lastmod map of %d entry(ies) rather "
                "than publishing a truncated one of %d",
                len(getattr(self, "_sitemap_expansion_failures", ()) or ()),
                len(previous),
                len(new_map),
            )
            return new_urls
        self._sitemap_lastmod_map = new_map
        return new_urls

    @staticmethod
    def _is_elog_url(url: str) -> bool:
        """Return True if the URL looks like an ELOG logbook index (fallback heuristic).
        Prefer the explicit 'elog-' prefix in input lists over this auto-detection.
        """
        from urllib.parse import urlparse

        path = urlparse(url).path.lower()
        return "/elog/" in path or "/elogs/" in path

    def _is_indico_url(self, url: str) -> bool:
        """Return True if the URL looks like an Indico event page.

        Matches when the path contains ``/event/`` and either:
        - the hostname equals the configured ``indico.base_url`` hostname, or
        - the hostname contains the word ``indico`` (covers any Indico instance,
          e.g. indico.cern.ch, indico.stfc.ac.uk, indico.fnal.gov, ...).

        The explicit ``indico-`` prefix in input lists is still supported and
        takes precedence over this auto-detection.
        """
        if not self.indico_enabled:
            return False
        from urllib.parse import urlparse

        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        if "/event/" not in path:
            return False
        configured_host = urlparse(self.indico_config.get("base_url", "")).hostname
        if configured_host and hostname == configured_host:
            return True
        return "indico" in hostname

    def _resolve_scraper(self):
        class_name = self.selenium_config.get("selenium_class")
        class_map = self.selenium_config.get("selenium_class_map", {})
        selenium_url = self.selenium_config.get("selenium_url", None)

        entry = class_map.get(class_name)

        if not entry:
            logger.error(
                f"Selenium class {class_name} is not defined in the configuration"
            )
            return None, {}

        scraper_class = entry.get("class")
        if isinstance(scraper_class, str):
            module_name = entry.get(
                "module",
                "src.data_manager.collectors.scrapers.integrations.sso_scraper",
            )
            module = importlib.import_module(module_name)
            scraper_class = getattr(module, scraper_class)
        scraper_kwargs = entry.get("kwargs", {})
        scraper_kwargs["selenium_url"] = selenium_url
        return scraper_class, scraper_kwargs

    def _handle_standard_url(
        self,
        url: str,
        persistence: PersistenceService,
        output_dir: Path,
        max_depth: int,
        client=None,
        use_client_for_scraping: bool = False,
        scraper: Optional[LinkScraper] = None,
        on_request_url: Optional[Callable[[str], None]] = None,
    ) -> int:
        """Scrape a URL and persist resources. Returns count of resources scraped."""
        # Parallel seed crawls pass their own per-worker scraper; the sequential
        # and selenium/SSO callers fall back to the shared ``self.web_scraper``.
        scraper = scraper if scraper is not None else self.web_scraper

        lastmod_map: Dict[str, str] = getattr(self, "_sitemap_lastmod_map", {})
        count = 0
        try:
            for resource in scraper.crawl_iter(
                url,
                browserclient=client,
                max_depth=max_depth,
                selenium_scrape=use_client_for_scraping,
                max_pages=self.max_pages,
                on_request_url=on_request_url,
            ):
                if lastmod_map:
                    try:
                        norm = normalize_page_url(resource.url)
                    except ValueError:
                        norm = resource.url
                    lm = lastmod_map.get(norm)
                    if lm is not None:
                        resource.metadata["last_modified"] = lm
                persistence.persist_resource(resource, output_dir)
                count += 1
            logger.info(f"Scraped {count} resources from {url}")
        except Exception as exc:
            logger.error(f"Failed to scrape {url}: {exc}", exc_info=exc)
        return count

    def _extract_urls_from_file(self, path: Path) -> List[str]:
        """Extract URLs from file, ignoring depth specifications for now."""
        urls: List[str] = []
        with path.open("r") as file:
            for line in file:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                # Extract just the URL part, ignoring depth specification if present
                url_depth = stripped.split(",")
                url = url_depth[0].strip()
                urls.append(url)
        return urls

    def _collect_git_resources(
        self,
        git_urls: List[str],
        persistence: PersistenceService,
        git_dir: Path,
    ) -> List[ScrapedResource]:
        git_scraper = self._get_git_scraper()
        resources = git_scraper.collect(git_urls)
        for resource in resources:
            persistence.persist_resource(resource, git_dir)
        return resources

    def _get_git_scraper(self) -> "GitScraper":
        if self._git_scraper is None:
            from src.data_manager.collectors.scrapers.integrations.git_scraper import (
                GitScraper,
            )

            self._git_scraper = GitScraper(manager=self, git_config=self.git_config)
        return self._git_scraper

    def _collect_indico_resources(
        self,
        indico_urls: List[str],
        persistence: PersistenceService,
        indico_dir: Path,
    ) -> List[ScrapedResource]:
        """Collect Indico events and materials."""
        indico_scraper = self._get_indico_scraper()
        resources = indico_scraper.collect(indico_urls)
        for resource in resources:
            persistence.persist_resource(resource, indico_dir)
        return resources

    def _get_indico_scraper(self) -> "IndicoScraper":
        if self._indico_scraper is None:
            from src.data_manager.collectors.scrapers.integrations.indico_scraper import (
                IndicoScraper,
            )

            self._indico_scraper = IndicoScraper(
                manager=self, indico_config=self.indico_config
            )
        return self._indico_scraper
