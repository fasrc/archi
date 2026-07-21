## Context

The web scraper's `_normalize_url` (`src/data_manager/collectors/scrapers/scraper.py`,
~line 302) currently:

```python
def _normalize_url(self, url: str) -> Optional[str]:
    if not url:
        return None
    normalized, _ = urldefrag(url)
    parsed = urlparse(normalized)
    if not parsed.scheme:
        return normalized
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
    ).geturl()
```

It lowercases scheme/netloc and strips the fragment, but does not touch the path. Because
`docs.rc.fas.harvard.edu` 301-redirects `/kb/x` → `/kb/x/`, both slash forms survive
normalization as distinct strings. `_normalize_url` is the single chokepoint feeding the
crawl frontier (`visited_urls`/`seen_urls` via `_mark_visited`, ~line 316) and
`get_links_with_same_hostname` (~line 324), so canonicalizing there dedups both the crawl
frontier and what is ultimately persisted/embedded.

## Goals / Non-Goals

**Goals:**
- Make `…/path/` and `…/path` normalize to one identical string.
- Preserve the site-root lone `/` (never reduce `https://host/` to an empty path).
- Preserve query/params unchanged, applying the slash strip to the path only.
- Keep the existing contract for empty/`None` and schemeless input.
- Cover the new branch with a unit test (diff-cover ≥80% on the pure function).

**Non-Goals:**
- No re-ingest or backfill of already-stored duplicate documents — that is a deploy-time
  step (redeploy re-ingest) verified on the dev host, out of scope for this code change.
- No change to `resource_hash`, persistence, embedding, or config schema.
- No general RFC-3986 normalization (percent-encoding case, dot-segment resolution,
  default-port stripping, query re-ordering). Scope is strictly the trailing-slash twin.

## Decisions

**Decision: Strip a trailing slash from `parsed.path` when the path is longer than `/`.**
Rebuild the URL by extending the existing `parsed._replace(...)` call with a
`path=`-computed value. Compute it as: if `parsed.path` ends with `/` and is longer than
one character, strip the trailing slash(es) down to (but not through) the root; otherwise
leave it. Rationale: the redirect only ever adds a single trailing slash, but being
tolerant of `//` avoids a second class of twin; the root guard keeps `https://host/`
valid.
- *Alternative considered:* `rstrip("/")` unconditionally — rejected: it turns
  `https://host/` into `https://host` (empty path), which the spec forbids and which could
  break base-URL joins in `get_links_with_same_hostname`.
- *Alternative considered:* normalize at persist/dedup time instead of crawl time —
  rejected: it would not stop the crawler from double-fetching, and it duplicates logic;
  `_normalize_url` is the correct single seam.

**Decision: Path-only strip; leave query/params untouched.**
Operate on `parsed.path` and let `geturl()` reassemble query/params/scheme/netloc. This
makes `…/x/?a=1` and `…/x?a=1` collapse while preserving `a=1`. Rationale: matches the
issue's "handle query/params sanely" and avoids lossy query rewriting.

**Decision: Leave the schemeless early-return path as-is.**
The `if not parsed.scheme: return normalized` branch stays; the crawler resolves relative
links to absolute via `urljoin` before normalizing (`get_links_with_same_hostname`), so
absolute URLs are what actually populate the dedup sets. Keeping the early return avoids
changing behavior for inputs the function was never meant to fully canonicalize.

**Decision: TDD — failing test first.**
Add a `tests/unit/` test constructing/instantiating the scraper (or calling the method on
a minimal instance) that asserts (a) slash/no-slash collapse, (b) root preserved, (c)
query consistency, (d) empty→`None` and schemeless-no-raise. Watch (a) fail against the
current implementation, then implement the strip.

## Risks / Trade-offs

- **[Over-stripping a semantically-significant trailing slash]** Some servers treat
  `/x/` and `/x` as different resources. → For the archi corpus the relevant host
  (`docs.rc.fas`) 301-redirects one to the other, so they are the same resource; scope is
  the crawler's own dedup frontier, and any residual difference is resolved by the fetch
  following the redirect anyway.
- **[Root/empty-path edge cases]** `https://host` (no path) vs `https://host/`. → Explicit
  scenarios pin both: root `/` preserved, empty path not given a spurious slash.
- **[Corpus/benchmark skew]** Collapsing twins changes retrieval results. → Called out in
  proposal Impact: land before locking a RAGAS baseline; do not compare pre/post runs.

## Migration Plan

1. Land the code + unit test via the gate on `fix/issue-118-scraper-trailing-slash`; PR to
   `fasrc/archi:dev`. No data migration in this change.
2. (Deploy-time, human/dev-host, not part of this PR's gate) After merge, a dev redeploy
   re-ingest collapses the existing twins. Verify with the `dup_groups` SQL from issue #118
   returning `0` and the docs.rc.fas embedded page count dropping ~181.
3. **Rollback:** revert the one-method change; behavior returns to the prior (duplicating)
   normalizer. No schema or data rollback needed.

## Open Questions

- None blocking. Multi-trailing-slash tolerance (`//`) is included defensively but is not
  required by the issue; the single-slash redirect twin is the confirmed case.
