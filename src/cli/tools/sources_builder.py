"""Build an importable web ``sources.list`` from a typed YAML manifest.

This is the implementation behind ``archi sources build`` (registered in
``src/cli/cli_main.py``). It is intentionally a thin, dependency-light helper
module that mirrors ``src/cli/tools/config_seed.py``:

- ``load_manifest`` parses + validates a YAML list of typed seeds.
- ``expand_sitemap`` / ``crawl_same_host`` / the ``literal`` branch turn each
  seed into page URLs (network fetched via ``requests``; sitemap parsed with the
  stdlib ``xml.etree.ElementTree``; crawl link extraction via ``beautifulsoup4``).
- ``apply_globs`` / ``normalize_url`` / ``render_list`` / ``append_manual_extras``
  filter, normalize, dedupe, and render the wholesale-regenerated list.
- ``sources_build_entry`` is the CLI entry point wiring it all together,
  including ``--output`` resolution, ``--dry-run`` diff, and the ``--import``
  shell-out.

Design points (see openspec/changes/add-sources-build-command): wholesale
regeneration, deterministic normalization across ALL emitted URLs, one level of
``<sitemapindex>`` nesting, fail-the-build-on-fetch-error, and a generated block
that wins position over ``manual-extras.list``.
"""

import difflib
import fnmatch
import shlex
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import click
import requests

# Sitemap XML namespace (sitemaps.org). Documents may or may not declare it; we
# match ``<loc>`` namespace-agnostically by stripping the namespace from tags.
_SITEMAP_TIMEOUT = 30
_VALID_TYPES = {"sitemap", "crawl", "literal"}
_EXTRA_PREFIXES = ("git-", "sso-", "elog-", "indico-")


class ManifestError(Exception):
    """Raised when a manifest is not valid YAML or has a malformed entry."""


class FetchError(Exception):
    """Raised when a sitemap/crawl fetch fails (non-200, timeout, bad body)."""


class OutputResolutionError(Exception):
    """Raised when the default ``--output`` cannot be resolved unambiguously."""


# --------------------------------------------------------------------------- #
# 1. Manifest schema
# --------------------------------------------------------------------------- #
def load_manifest(path: str) -> List[Dict]:
    """Load and validate a typed-seed YAML manifest.

    Returns the list of seed dicts. Raises :class:`ManifestError` (naming the
    offending entry) on invalid YAML, a non-list document, or any entry with a
    missing/unknown ``type`` or a missing ``url``. No output is written here, so
    a raised error leaves any target file untouched.
    """
    import yaml

    try:
        with open(path, "r") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError:
        raise ManifestError(f"Manifest not found: {path}")
    except yaml.YAMLError as exc:
        raise ManifestError(f"Manifest is not valid YAML ({path}): {exc}")

    if not isinstance(data, list):
        raise ManifestError(
            f"Manifest must be a YAML list of seed entries, got "
            f"{type(data).__name__}: {path}"
        )

    seeds: List[Dict] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ManifestError(
                f"Manifest entry #{index + 1} must be a mapping, got "
                f"{type(entry).__name__}: {entry!r}"
            )
        seed_type = entry.get("type")
        if seed_type not in _VALID_TYPES:
            raise ManifestError(
                f"Manifest entry #{index + 1} has unknown type "
                f"{seed_type!r} (expected one of {sorted(_VALID_TYPES)}): "
                f"{entry!r}"
            )
        if not entry.get("url"):
            raise ManifestError(
                f"Manifest entry #{index + 1} ({seed_type}) is missing a "
                f"'url' field: {entry!r}"
            )
        seeds.append(entry)
    return seeds


# --------------------------------------------------------------------------- #
# 2. Sitemap expansion
# --------------------------------------------------------------------------- #
def _local_tag(tag: str) -> str:
    """Strip an XML namespace, returning the local tag name (e.g. ``loc``)."""
    return tag.rsplit("}", 1)[-1]


def _fetch_text(url: str) -> str:
    """GET ``url`` and return the body text, raising :class:`FetchError` on any
    non-200, connection, or timeout failure."""
    try:
        resp = requests.get(url, timeout=_SITEMAP_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        raise FetchError(f"Failed to fetch {url}: {exc}")
    if resp.status_code != 200:
        raise FetchError(f"Fetch of {url} returned HTTP {resp.status_code}")
    return resp.text


def _parse_xml(url: str, text: str) -> "ElementTree.Element":
    """Parse XML text, raising :class:`FetchError` on a malformed body."""
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise FetchError(f"Malformed XML at {url}: {exc}")


def _locs(root: "ElementTree.Element") -> List[str]:
    """Return every ``<loc>`` text under ``root`` (namespace-agnostic)."""
    out: List[str] = []
    for elem in root.iter():
        if _local_tag(elem.tag) == "loc" and elem.text:
            out.append(elem.text.strip())
    return out


def expand_sitemap(url: str) -> List[str]:
    """Fetch a sitemap and emit page URLs, following one level of nesting.

    - A ``<urlset>`` document contributes every ``<loc>`` as a page URL.
    - A ``<sitemapindex>`` document's ``<loc>`` values are child *sitemap
      documents*; each is fetched exactly once. A child ``<urlset>``
      contributes its ``<loc>`` page URLs; a child that is itself a
      ``<sitemapindex>`` is NOT followed and contributes no URLs (its ``<loc>``
      values are sitemap docs, not pages — D8).
    - An empty ``<urlset>`` is valid and contributes nothing.

    Raises :class:`FetchError` on any fetch/parse failure (D7).
    """
    root = _parse_xml(url, _fetch_text(url))
    if _local_tag(root.tag) == "urlset":
        return _locs(root)

    if _local_tag(root.tag) == "sitemapindex":
        pages: List[str] = []
        for child_url in _locs(root):
            child_root = _parse_xml(child_url, _fetch_text(child_url))
            # Only a child <urlset> yields page URLs; a nested <sitemapindex>
            # is not followed and contributes nothing.
            if _local_tag(child_root.tag) == "urlset":
                pages.extend(_locs(child_root))
        return pages

    # Unknown root element: treat as malformed.
    raise FetchError(f"Unexpected sitemap root <{_local_tag(root.tag)}> at {url}")


# --------------------------------------------------------------------------- #
# 3. Glob filtering
# --------------------------------------------------------------------------- #
def apply_globs(
    urls: List[str],
    include: Optional[List[str]],
    exclude: Optional[List[str]],
) -> List[str]:
    """Filter ``urls`` by ``fnmatch`` glob lists.

    Keep a URL only if it matches at least one ``include`` glob (when any are
    given) and matches no ``exclude`` glob. ``literal`` seeds bypass this.
    """
    include = include or []
    exclude = exclude or []
    kept: List[str] = []
    for url in urls:
        if include and not any(fnmatch.fnmatch(url, pat) for pat in include):
            continue
        if any(fnmatch.fnmatch(url, pat) for pat in exclude):
            continue
        kept.append(url)
    return kept


# --------------------------------------------------------------------------- #
# URL normalization (applied to ALL emitted URLs, including literals — D6)
# --------------------------------------------------------------------------- #
def normalize_url(url: str) -> str:
    """Normalize a URL for stable, deterministic output.

    Drops the fragment, lowercases the scheme and host, and collapses a single
    trailing path slash (so ``…/page`` and ``…/page/`` are one entry). The root
    path ``/`` is preserved. Applied uniformly to ``sitemap``, ``crawl``, and
    ``literal`` URLs (``literal`` means "not fetched/crawled/filtered", not
    "bytes preserved" — D6). ``manual-extras.list`` lines are the one verbatim
    exception and are never passed through here.
    """
    parts = urlparse(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    # Drop fragment; keep query verbatim.
    return urlunparse((scheme, netloc, path, parts.params, parts.query, ""))


# --------------------------------------------------------------------------- #
# 4. Deterministic same-host crawl
# --------------------------------------------------------------------------- #
def _extract_links(base_url: str, html: str) -> List[str]:
    """Extract absolute anchor hrefs from ``html`` resolved against ``base_url``.

    Relative links are resolved with :func:`urllib.parse.urljoin`. Anchors
    without an ``href`` are skipped. Raises :class:`FetchError` if the body is
    not parseable as HTML.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - declared in pyproject
        raise FetchError(f"beautifulsoup4 is required for crawl seeds: {exc}")

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        raise FetchError(f"Malformed HTML at {base_url}: {exc}")

    links: List[str] = []
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not href:
            continue
        links.append(urljoin(base_url, href))
    return links


def crawl_same_host(
    url: str,
    depth: int,
    include: Optional[List[str]],
    exclude: Optional[List[str]],
) -> List[str]:
    """Crawl an index page and return its same-host links, normalized + sorted.

    Fetches ``url``, extracts anchors, resolves relatives, and keeps only links
    whose host matches the seed host. Honors ``depth`` (BFS over same-host
    pages; default one level) and the seed's include/exclude globs. Output is
    sorted for deterministic ordering. Raises :class:`FetchError` on any
    fetch/parse failure (D7).
    """
    seed_host = urlparse(url).netloc.lower()
    depth = depth if depth and depth > 0 else 1

    discovered: List[str] = []
    seen = set()
    # Frontier holds the ORIGINAL (un-normalized) page URLs so relative links
    # resolve correctly via urljoin — collapsing a trailing slash on the base
    # would change relative resolution. Normalization is applied only to the
    # discovered links (for host compare, dedupe, and output).
    frontier = [url]
    visited_pages = set()

    for _ in range(depth):
        next_frontier: List[str] = []
        for page in frontier:
            if page in visited_pages:
                continue
            visited_pages.add(page)
            html = _fetch_text(page)
            for link in _extract_links(page, html):
                norm = normalize_url(link)
                if urlparse(norm).netloc.lower() != seed_host:
                    continue
                if norm not in seen:
                    seen.add(norm)
                    discovered.append(norm)
                    next_frontier.append(link)
        frontier = next_frontier

    filtered = apply_globs(discovered, include, exclude)
    return sorted(filtered)


# --------------------------------------------------------------------------- #
# 5. Seed dispatcher (sitemap / crawl / literal)
# --------------------------------------------------------------------------- #
def expand_seed(seed: Dict) -> List[str]:
    """Expand a single validated seed into a list of normalized page URLs.

    - ``sitemap``: fetch + expand, then apply globs, then normalize.
    - ``crawl``: crawl same-host (already normalized + glob-filtered + sorted).
    - ``literal``: emit the URL verbatim — never fetched/crawled/glob-filtered —
      but still normalized (D6).
    """
    seed_type = seed["type"]
    url = seed["url"]
    include = seed.get("include") or []
    exclude = seed.get("exclude") or []

    if seed_type == "literal":
        return [normalize_url(url)]

    if seed_type == "sitemap":
        raw = expand_sitemap(url)
        filtered = apply_globs(raw, include, exclude)
        return [normalize_url(u) for u in filtered]

    if seed_type == "crawl":
        depth = seed.get("depth", 1)
        return crawl_same_host(url, depth, include, exclude)

    # Unreachable: load_manifest already rejected unknown types.
    raise ManifestError(f"Unsupported seed type: {seed_type!r}")


# --------------------------------------------------------------------------- #
# 6. Render / dedupe / manual-extras
# --------------------------------------------------------------------------- #
def render_list(seed_urls: List[str]) -> str:
    """Render generated URLs into list text: one per line, deduped preserving
    first-seen order, with a trailing newline. Inputs are assumed normalized."""
    deduped: List[str] = []
    seen = set()
    for url in seed_urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return "".join(f"{url}\n" for url in deduped)


def append_manual_extras(generated_urls: List[str], output_path: str) -> List[str]:
    """Return the final list of lines: the deduped generated block followed by a
    sibling ``manual-extras.list`` (if present), appended verbatim.

    Extras rules (D4): comment (``#``) and blank lines are skipped; non-comment
    entries are kept verbatim — preserving ``git-``/``sso-``/``elog-``/``indico-``
    prefixes — and are never fetched/crawled. The generated block wins position:
    an extras line whose value duplicates an already-emitted generated URL is
    dropped so the URL appears exactly once, in its generated position. A
    prefixed extras line has no generated counterpart and is always retained.
    """
    # Dedupe the generated block first (first-seen order).
    lines: List[str] = []
    seen = set()
    for url in generated_urls:
        if url not in seen:
            seen.add(url)
            lines.append(url)

    extras_path = Path(output_path).parent / "manual-extras.list"
    if not extras_path.exists():
        return lines

    for raw in extras_path.read_text().splitlines():
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry in seen:
            # Duplicates a generated URL — generated block wins position.
            continue
        seen.add(entry)
        lines.append(entry)
    return lines


# --------------------------------------------------------------------------- #
# 7. Output path resolution
# --------------------------------------------------------------------------- #
def _collect_input_lists(config_path: str) -> List[str]:
    """Aggregate ``data_manager.sources.links.input_lists`` from a config file.

    Mirrors ``ConfigurationManager._collect_input_lists`` semantics for a single
    config (the relevant entries are read directly from the YAML, avoiding the
    full validation/jinja cycle which is irrelevant to output resolution).
    """
    import yaml

    with open(config_path, "r") as handle:
        config = yaml.safe_load(handle) or {}
    data_manager = config.get("data_manager", {}) or {}
    sources_section = data_manager.get("sources", {}) or {}
    links_section = (
        sources_section.get("links", {}) if isinstance(sources_section, dict) else {}
    )
    lists = links_section.get("input_lists") or []
    if not isinstance(lists, list):
        return []
    # Preserve config order while de-duplicating.
    seen = set()
    out: List[str] = []
    for entry in lists:
        if entry not in seen:
            seen.add(entry)
            out.append(entry)
    return out


def resolve_output_path(output: Optional[str], config: Optional[str]) -> str:
    """Resolve the target list path.

    ``--output`` always wins when given. Otherwise the path is resolved from the
    config's ``input_lists``, which is a list: a default is produced ONLY when
    exactly one entry is configured. Zero or several entries (or no config) raise
    :class:`OutputResolutionError` instructing the operator to pass ``--output``.
    """
    if output:
        return output
    if not config:
        raise OutputResolutionError(
            "No --output given and no -c/--config to resolve it from. "
            "Pass --output explicitly."
        )
    entries = _collect_input_lists(config)
    if len(entries) == 1:
        return entries[0]
    raise OutputResolutionError(
        f"Cannot resolve a default output: the config declares {len(entries)} "
        f"'input_lists' entries (need exactly one). Pass --output explicitly."
    )


# --------------------------------------------------------------------------- #
# 8. Dry-run diff
# --------------------------------------------------------------------------- #
def compute_diff(final_lines: List[str], output_path: str) -> str:
    """Return a unified diff of ``final_lines`` against the existing output file.

    When the output file does not yet exist, the diff is computed against an
    empty file. Used by ``--dry-run`` to print proposed changes without writing.
    """
    path = Path(output_path)
    if path.exists():
        existing = path.read_text().splitlines(keepends=True)
    else:
        existing = []
    proposed = [f"{line}\n" for line in final_lines]
    diff = difflib.unified_diff(
        existing,
        proposed,
        fromfile=str(path),
        tofile=f"{path} (proposed)",
    )
    return "".join(diff)


# --------------------------------------------------------------------------- #
# 10. Import trigger (shell out to `archi create … --force`)
# --------------------------------------------------------------------------- #
def trigger_import(
    name: str,
    config: str,
    services: str,
    env_file: Optional[str],
) -> None:
    """Trigger a deployment refresh equivalent to ``archi create … --force``.

    ``archi create`` requires a single ``--config`` AND a non-empty
    ``--services`` (it calls ``validate_services_selection``), so ``services``
    must be non-empty (default ``chatbot``). ``--env-file`` is forwarded when
    given. Raises :class:`click.ClickException` on a non-zero refresh exit.
    """
    parts = [
        "archi",
        "create",
        "--name",
        name,
        "--config",
        config,
        "--services",
        services,
    ]
    if env_file:
        parts += ["--env-file", env_file]
    parts.append("--force")
    command = " ".join(shlex.quote(p) for p in parts)

    # Imported lazily: CommandRunner pulls in src.utils (flask/rbac), which the
    # pure-function paths of this module must not require to import.
    from src.cli.utils.command_runner import CommandRunner

    stdout, stderr, exit_code = CommandRunner.run_simple(command)
    if exit_code != 0:
        raise click.ClickException(
            f"Import refresh failed (exit {exit_code}): {stderr or stdout}"
        )


# --------------------------------------------------------------------------- #
# 9. Orchestration entry point (called from the CLI command)
# --------------------------------------------------------------------------- #
def sources_build_entry(
    manifest: str,
    config: Optional[str],
    output: Optional[str],
    name: Optional[str],
    services: str,
    env_file: Optional[str],
    do_import: bool,
    dry_run: bool,
) -> None:
    """End-to-end build: parse manifest → expand seeds → render → write/diff →
    optional import. Raises :class:`click.ClickException` on any user-facing
    error so the CLI exits non-zero without writing a partial list.
    """
    # --import / --dry-run are mutually exclusive; --import needs name + config.
    if do_import and dry_run:
        raise click.ClickException("--import is incompatible with --dry-run.")
    if do_import and not name:
        raise click.ClickException("--import requires --name <deployment>.")
    if do_import and not config:
        raise click.ClickException("--import requires -c/--config.")

    # Resolve the target path BEFORE any network work so an ambiguous output
    # fails fast and writes nothing.
    try:
        target = resolve_output_path(output, config)
    except OutputResolutionError as exc:
        raise click.ClickException(str(exc))

    # Parse + validate the manifest (no write on failure).
    try:
        seeds = load_manifest(manifest)
    except ManifestError as exc:
        raise click.ClickException(str(exc))

    # Expand every seed; any fetch error aborts the whole build (D7).
    generated: List[str] = []
    try:
        for seed in seeds:
            generated.extend(expand_seed(seed))
    except FetchError as exc:
        raise click.ClickException(str(exc))

    # Dedupe generated block (first-seen) then append manual-extras.
    final_lines = append_manual_extras(generated, target)

    if dry_run:
        diff = compute_diff(final_lines, target)
        click.echo(diff, nl=False)
        return

    # Write the regenerated list wholesale.
    out_path = Path(target)
    if out_path.parent and not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(f"{line}\n" for line in final_lines))
    click.echo(f"Wrote {len(final_lines)} URLs to {target}")

    if do_import:
        trigger_import(name, config, services, env_file)
        click.echo(f"Triggered import refresh for deployment '{name}'")
