# web-crawl-url-canonicalization Specification

## Purpose
Define how the web scraper reduces differently-spelled URLs for the same page to one
canonical string, so a crawl does not ingest the same document twice. Trailing-slash
variants (`/kb/x/` vs `/kb/x`) were producing duplicate pages from
`docs.rc.fas.harvard.edu`; canonicalization happens in `_normalize_url`, alongside the
pre-existing scheme/netloc lowercasing and fragment stripping.

## Requirements

### Requirement: Trailing-slash path canonicalization

The web scraper's `_normalize_url` SHALL canonicalize an absolute HTTP(S) URL's path so
that a form ending in a single trailing slash and the otherwise-identical form without it
normalize to the same string. It SHALL strip a trailing `/` from the path only when the
path is longer than the site root (`/`), leaving scheme-lowercasing, netloc-lowercasing,
and fragment-stripping (the existing behavior) intact.

#### Scenario: Slash and no-slash variants collapse

- **WHEN** `_normalize_url` is called with `https://docs.rc.fas.harvard.edu/kb/x/` and again with `https://docs.rc.fas.harvard.edu/kb/x`
- **THEN** both calls return the identical normalized string (the no-slash form)

#### Scenario: Deep path with a trailing slash is stripped

- **WHEN** `_normalize_url` is called with `https://host/a/b/c/`
- **THEN** it returns `https://host/a/b/c` (path trailing slash removed)

### Requirement: Site root is preserved

The normalizer SHALL NOT strip the lone root slash of a site: a URL whose path is exactly
`/` (e.g. `https://host/`) keeps that root slash rather than being reduced to an empty
path, so the site root remains a valid, resolvable normalized URL.

#### Scenario: Root slash preserved

- **WHEN** `_normalize_url` is called with `https://host/`
- **THEN** it returns `https://host/` (the root `/` is not stripped)

#### Scenario: Empty-path URL is unchanged

- **WHEN** `_normalize_url` is called with `https://host` (no path)
- **THEN** it returns a normalized string that is not broken by trailing-slash handling (no spurious slash added or removed)

### Requirement: Query and params are preserved consistently

The trailing-slash canonicalization SHALL apply to the URL path only, leaving the query
string and params intact, so that `…/x/?a=1` and `…/x?a=1` collapse to the same
normalized string and no query data is lost.

#### Scenario: Query survives path canonicalization

- **WHEN** `_normalize_url` is called with `https://host/x/?a=1` and again with `https://host/x?a=1`
- **THEN** both return the identical normalized string, and that string still carries the `a=1` query

### Requirement: Non-normalizable input is unchanged

The normalizer SHALL preserve its existing contract for inputs it cannot fully normalize:
an empty/`None` URL returns `None`, and a schemeless (relative) URL is returned without
raising. Trailing-slash handling MUST NOT introduce a new failure mode for these inputs.

#### Scenario: Empty input returns None

- **WHEN** `_normalize_url` is called with `""` or `None`
- **THEN** it returns `None`

#### Scenario: Schemeless URL does not raise

- **WHEN** `_normalize_url` is called with a relative URL such as `/kb/x/`
- **THEN** it returns without raising an exception
