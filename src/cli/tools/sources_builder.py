"""Build an importable web ``sources.list`` from a typed YAML manifest.

This is the implementation behind ``archi sources build`` (registered in
``src/cli/cli_main.py``). It is intentionally a thin, dependency-light helper
module that mirrors ``src/cli/tools/config_seed.py``:

- ``load_manifest`` parses + validates a YAML list of typed seeds.
- ``expand_sitemap`` / ``crawl_same_host`` / the ``literal`` branch turn each
  seed into page URLs (network fetched via ``requests``; sitemap parsed with the
  stdlib ``xml.etree.ElementTree``; crawl link extraction via ``beautifulsoup4``).
- ``apply_globs`` / ``normalize_url`` / ``append_manual_extras`` filter,
  normalize, dedupe, and render the wholesale-regenerated list.
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
# Cap fetched bodies to bound memory and blunt decompression/oversize abuse. A
# real sitemap or index page is well under this; the protocol caps a single
# sitemap at 50 MB uncompressed, so 64 MB is generous headroom.
_MAX_FETCH_BYTES = 64 * 1024 * 1024
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
        # Normalize the optional glob lists: a YAML scalar string is coerced to
        # a one-item list (otherwise fnmatch would iterate it char-by-char);
        # anything that is neither a string nor a list of strings is rejected.
        for field in ("include", "exclude"):
            if field in entry:
                entry[field] = _coerce_glob_list(index, seed_type, field, entry[field])
        # Validate the optional crawl depth as a positive int.
        if "depth" in entry:
            entry["depth"] = _coerce_depth(index, seed_type, entry["depth"])
        seeds.append(entry)
    return seeds


def _coerce_glob_list(index: int, seed_type, field: str, value) -> List[str]:
    """Coerce a manifest glob field to a list of strings or raise ManifestError."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return value
        raise ManifestError(
            f"Manifest entry #{index + 1} ({seed_type}) '{field}' must be a "
            f"string or a list of strings: {value!r}"
        )
    raise ManifestError(
        f"Manifest entry #{index + 1} ({seed_type}) '{field}' must be a string "
        f"or a list of strings, got {type(value).__name__}: {value!r}"
    )


def _coerce_depth(index: int, seed_type, value) -> int:
    """Coerce a manifest ``depth`` to a positive int or raise ManifestError.

    A bool is rejected (``True``/``False`` are ints in Python but never a valid
    depth); a numeric string like ``"2"`` is accepted and coerced.
    """
    if isinstance(value, bool):
        raise ManifestError(
            f"Manifest entry #{index + 1} ({seed_type}) 'depth' must be a "
            f"positive integer, got bool: {value!r}"
        )
    if isinstance(value, int):
        depth = value
    elif isinstance(value, str) and value.strip().isdigit():
        depth = int(value.strip())
    else:
        raise ManifestError(
            f"Manifest entry #{index + 1} ({seed_type}) 'depth' must be a "
            f"positive integer, got {type(value).__name__}: {value!r}"
        )
    if depth < 1:
        raise ManifestError(
            f"Manifest entry #{index + 1} ({seed_type}) 'depth' must be >= 1, "
            f"got {depth}"
        )
    return depth


# --------------------------------------------------------------------------- #
# 2. Sitemap expansion
# --------------------------------------------------------------------------- #
def _local_tag(tag: str) -> str:
    """Strip an XML namespace, returning the local tag name (e.g. ``loc``)."""
    return tag.rsplit("}", 1)[-1]


def _fetch_text(url: str, require_html: bool = False) -> "tuple[str, str]":
    """GET ``url`` and return ``(body_text, final_url)``.

    Raises :class:`FetchError` on any non-200, connection, timeout, over-size,
    or (when ``require_html``) non-HTML response. The body is streamed and the
    read aborts as soon as :data:`_MAX_FETCH_BYTES` is exceeded, so a hostile or
    misconfigured host cannot force the whole payload into memory first.

    ``final_url`` is the post-redirect URL (``resp.url``); the crawl uses it as
    the ``urljoin`` base so relative links resolve correctly when a seed/child
    redirects (e.g. slashless → trailing slash).
    """
    try:
        resp = requests.get(url, timeout=_SITEMAP_TIMEOUT, stream=True)
    except requests.exceptions.RequestException as exc:
        raise FetchError(f"Failed to fetch {url}: {exc}")
    try:
        if resp.status_code != 200:
            raise FetchError(f"Fetch of {url} returned HTTP {resp.status_code}")
        if require_html:
            content_type = resp.headers.get("Content-Type", "")
            # BeautifulSoup does not raise on non-HTML (it would silently yield
            # zero links and quietly shrink the regenerated list), so a crawl
            # seed must reject a non-HTML 200 explicitly.
            ctype_main = content_type.split(";", 1)[0].strip().lower()
            if ctype_main and ctype_main not in ("text/html", "application/xhtml+xml"):
                raise FetchError(
                    f"Crawl seed {url} returned non-HTML Content-Type "
                    f"{content_type!r}"
                )
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_FETCH_BYTES:
                raise FetchError(
                    f"Body from {url} exceeds the {_MAX_FETCH_BYTES}-byte cap"
                )
            chunks.append(chunk)
    except requests.exceptions.RequestException as exc:
        raise FetchError(f"Failed to read {url}: {exc}")
    finally:
        resp.close()
    encoding = resp.encoding or resp.apparent_encoding or "utf-8"
    text = b"".join(chunks).decode(encoding, errors="replace")
    return text, resp.url


def _parse_xml(url: str, text: str) -> "ElementTree.Element":
    """Parse XML text, raising :class:`FetchError` on a malformed body.

    Rejects a DTD/entity declaration (``<!DOCTYPE`` / ``<!ENTITY``) before
    parsing: the stdlib ``xml.etree`` parser is vulnerable to billion-laughs
    internal-entity expansion (CPython does not resolve external entities, but
    nested internal entities can still blow up memory). A legitimate sitemap
    never declares a DTD, so refusing one is safe and closes the vector without
    pulling in a new dependency.
    """
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise FetchError(
            f"Refusing sitemap with a DTD/entity declaration at {url} "
            f"(possible entity-expansion attack)"
        )
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise FetchError(f"Malformed XML at {url}: {exc}")


def _locs(root: "ElementTree.Element", wrapper: str) -> List[str]:
    """Return the ``<loc>`` text of each DIRECT ``<wrapper>`` child of ``root``.

    For a ``<urlset>`` pass ``wrapper="url"``; for a ``<sitemapindex>`` pass
    ``wrapper="sitemap"``. Only the ``<loc>`` immediately inside a direct
    wrapper child is read — descent is deliberately shallow so an inline-nested
    ``<sitemapindex>`` buried inside a child element contributes nothing (D8).
    """
    out: List[str] = []
    for child in root:
        if _local_tag(child.tag) != wrapper:
            continue
        for grand in child:
            if _local_tag(grand.tag) == "loc" and grand.text:
                out.append(grand.text.strip())
                break  # one <loc> per <url>/<sitemap> wrapper
    return out


def expand_sitemap(url: str) -> List[str]:
    """Fetch a sitemap and emit page URLs, following one level of nesting.

    - A ``<urlset>`` document contributes the ``<loc>`` of each direct ``<url>``.
    - A ``<sitemapindex>`` document's direct ``<sitemap>`` ``<loc>`` values are
      child *sitemap documents*; each is fetched exactly once. A child
      ``<urlset>`` contributes its page URLs; a child that is itself a
      ``<sitemapindex>`` is NOT followed and contributes no URLs (its ``<loc>``
      values are sitemap docs, not pages — D8). An inline-nested
      ``<sitemapindex>`` buried inside a ``<sitemap>`` is likewise ignored,
      because only direct ``<sitemap>`` children are read.
    - An empty ``<urlset>`` is valid and contributes nothing.

    Raises :class:`FetchError` on any fetch/parse failure (D7).
    """
    text, _ = _fetch_text(url)
    root = _parse_xml(url, text)
    if _local_tag(root.tag) == "urlset":
        return _locs(root, "url")

    if _local_tag(root.tag) == "sitemapindex":
        pages: List[str] = []
        for child_url in _locs(root, "sitemap"):
            child_text, _ = _fetch_text(child_url)
            child_root = _parse_xml(child_url, child_text)
            # Only a child <urlset> yields page URLs; a nested <sitemapindex>
            # is not followed and contributes nothing.
            if _local_tag(child_root.tag) == "urlset":
                pages.extend(_locs(child_root, "url"))
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
    without an ``href``, and fragment-only hrefs (``#section``) that resolve to
    the page itself, are skipped. Raises :class:`FetchError` if the body is not
    parseable as HTML.
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
        # A fragment-only href (e.g. ``#section``) points back at the current
        # page; skip it so it isn't normalized into a duplicate of the page.
        if href.strip().startswith("#"):
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

    Fetches ``url``, extracts anchors, resolves relatives against the FINAL
    (post-redirect) URL, and keeps only links whose host matches the seed host
    (compared by ``hostname`` so ``host`` and ``host:443`` match). Honors
    ``depth`` (BFS over same-host pages; default one level) and the seed's
    include/exclude globs — globs are applied as links are discovered, so an
    excluded child is never fetched. Output is sorted for deterministic
    ordering. Raises :class:`FetchError` on any fetch/parse failure (D7).
    """
    seed_host = (urlparse(url).hostname or "").lower()
    depth = depth if depth and depth > 0 else 1
    include = include or []
    exclude = exclude or []

    def _keep(candidate: str) -> bool:
        if include and not any(fnmatch.fnmatch(candidate, p) for p in include):
            return False
        if any(fnmatch.fnmatch(candidate, p) for p in exclude):
            return False
        return True

    discovered: List[str] = []
    seen = set()
    # The frontier holds NORMALIZED page URLs, used both as the visited key and
    # (when re-fetched) the request target. visited_pages dedupes by the
    # normalized form so ``#section`` / trailing-slash variants aren't fetched
    # as distinct pages. The urljoin base is the FINAL (post-redirect) URL.
    frontier = [normalize_url(url)]
    visited_pages = set()

    for _ in range(depth):
        next_frontier: List[str] = []
        for page in frontier:
            if page in visited_pages:
                continue
            visited_pages.add(page)
            html, final_url = _fetch_text(page, require_html=True)
            for link in _extract_links(final_url, html):
                norm = normalize_url(link)
                if (urlparse(norm).hostname or "").lower() != seed_host:
                    continue
                if not _keep(norm):
                    # Excluded by globs — do not emit and do not fetch it.
                    continue
                if norm not in seen:
                    seen.add(norm)
                    discovered.append(norm)
                    next_frontier.append(norm)
        frontier = next_frontier

    return sorted(discovered)


# --------------------------------------------------------------------------- #
# 5. Seed dispatcher (sitemap / crawl / literal)
# --------------------------------------------------------------------------- #
def expand_seed(seed: Dict) -> List[str]:
    """Expand a single validated seed into a list of normalized page URLs.

    - ``sitemap``: fetch + expand, NORMALIZE, then apply globs (so globs match
      the same normalized form that is emitted, consistent with crawl).
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
        # Normalize BEFORE glob filtering so an include/exclude pattern matches
        # the same normalized URL that is emitted (e.g. a host-case or
        # trailing-slash difference can't make the glob miss).
        normalized = [normalize_url(u) for u in raw]
        return apply_globs(normalized, include, exclude)

    if seed_type == "crawl":
        depth = seed.get("depth", 1)
        return crawl_same_host(url, depth, include, exclude)

    # Unreachable: load_manifest already rejected unknown types.
    raise ManifestError(f"Unsupported seed type: {seed_type!r}")


# --------------------------------------------------------------------------- #
# 6. Dedupe / manual-extras
# --------------------------------------------------------------------------- #
def append_manual_extras(generated_urls: List[str], output_path: str) -> List[str]:
    """Return the final list of lines: the deduped generated block followed by a
    sibling ``manual-extras.list`` (if present), appended verbatim.

    Extras rules (D4): comment (``#``) and blank lines are skipped; non-comment
    entries are written verbatim and are never fetched/crawled. The generated
    block wins position. Dedupe is prefix-aware:

    - A PREFIXED extras line (``git-``/``sso-``/``elog-``/``indico-``) has no
      generated counterpart (the generated block holds bare URLs), so it is
      never normalized and is always retained.
    - An UNPREFIXED extras line is a URL: it is compared in NORMALIZED form
      against the generated URLs (which are themselves normalized), so a
      same-URL duplicate that merely differs by case / trailing slash / fragment
      is dropped — but the ORIGINAL line is written when it is kept.
    """
    # Dedupe the generated block first (first-seen order). Generated URLs are
    # already normalized by expand_seed, so this set is the normalized key set.
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
        if entry.startswith(_EXTRA_PREFIXES):
            # Prefixed: not a bare URL, never normalized, always retained.
            lines.append(entry)
            continue
        key = normalize_url(entry)
        if key in seen:
            # Duplicates a generated URL (modulo case/slash/fragment) — the
            # generated block wins position.
            continue
        seen.add(key)
        lines.append(entry)  # write the original line, not the normalized key
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
