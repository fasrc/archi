## ADDED Requirements

### Requirement: A scheduled link collection refreshes the sitemap lastmod map

The system SHALL re-expand the configured sitemap sources at the start of each scheduled link collection and scrape that collection under a `<lastmod>` map rebuilt from the values read just then, rather than under the map built when the data-manager process started.

A `<lastmod>` that advances while the service is running therefore reaches
`documents.last_modified` without a process restart. Before this requirement the map was
written once during initial ingest and never again, so every scheduled collection
re-persisted every page with the startup-era timestamp — the column was not merely
sometimes stale, it was stale by construction, and grew staler with process uptime.

#### Scenario: A changed lastmod reaches the map without a restart
- **WHEN** a page's sitemap `<lastmod>` changes after initial ingest and a scheduled link collection then runs in the same process
- **THEN** the lastmod map used for that collection holds the new value for that page
- **AND** the page's persisted `last_modified` reflects the new value, with no process restart

#### Scenario: A page that is still unchanged keeps its value
- **WHEN** a scheduled collection re-expands a sitemap whose `<lastmod>` values are unchanged
- **THEN** the refreshed map holds the same values as before and the persisted timestamps are unchanged

### Requirement: A failed refresh during a scheduled collection falls back to the previous map

The system SHALL, when sitemap expansion raises `SitemapExpansionError` during a scheduled link collection, log a warning, retain the previously built lastmod map, and proceed with the collection instead of failing it.

A stale-but-present map is strictly better than the always-stale map this change replaces,
and a transient DNS or server failure must not stop a scrape of URLs already in the
catalog. The retained map SHALL be the complete map from the last successful refresh — a
failed refresh SHALL NOT leave the map empty or partially rebuilt, because the persist path
stamps `last_modified` only while the map is non-empty and would otherwise silently drop
the timestamp from every page in that collection.

#### Scenario: Below-floor or over-cap expansion degrades instead of failing
- **WHEN** `SitemapExpansionError` is raised while refreshing the map for a scheduled link collection
- **THEN** a warning is logged
- **AND** the collection still runs over the catalog URLs
- **AND** the map from the last successful refresh is still in effect, entry for entry

#### Scenario: A failed refresh never blanks the map
- **WHEN** expansion fails partway through a scheduled refresh
- **THEN** the map is not empty and holds no partially rebuilt state, so pages are still stamped from the previous map

### Requirement: Initial ingest keeps its fail-fast expansion behavior

The system SHALL continue to let `SitemapExpansionError` propagate out of initial ingest, failing the ingest rather than shipping a corpus built from a below-floor or runaway sitemap.

The degraded fallback is a property of the scheduled path only. Initial ingest has no
previous map to fall back to, and its failure mode — an empty or runaway corpus — is the
one the floor and cap exist to prevent.

#### Scenario: Expansion error fails the initial ingest
- **WHEN** `SitemapExpansionError` is raised during initial collection from config
- **THEN** the error propagates to the caller and the ingest fails
- **AND** no fallback map is substituted and no warning-and-continue path is taken

### Requirement: A refreshed map excludes hand-listed pages

The system SHALL exclude from the refreshed map any page whose URL is hand-listed in the configured input lists, even when a sitemap also lists that page, so a hand-listed source's `last_modified` stays `NULL`.

Exclusion is by *normalized* page URL, matching how the persist path looks the map up, so a
hand-listed `/x/` and a sitemap-derived `/x` are recognized as the same page. This is the
rule initial ingest already applies by populating the map only for pages it actually
appends; the scheduled path SHALL apply the same rule, because its crawl set comes from the
catalog and so contains hand-listed and sitemap-derived pages alike.

#### Scenario: A page in both a hand list and a sitemap gets no timestamp
- **WHEN** a scheduled refresh expands a sitemap containing a page that is also hand-listed in the input lists
- **THEN** that page has no entry in the refreshed map and its persisted `last_modified` stays `NULL`

#### Scenario: A sitemap-only page does get its timestamp
- **WHEN** the same refresh expands a page that is not hand-listed
- **THEN** that page's normalized URL is in the refreshed map with its captured `lastmod`

### Requirement: Refreshing the map changes no fetch behavior

The system SHALL NOT change which pages any collection fetches, skips, or the order it collects them as a result of refreshing the map.

A scheduled collection's crawl set remains exactly the catalog query's result: a page newly
discovered in a sitemap since startup gains a map entry but is not scraped by that
collection. Initial ingest's crawl set — the hand list plus the not-already-listed expanded
pages, in that order — is unchanged by extracting the refresh into a shared helper.

#### Scenario: A newly discovered sitemap page is not added to the scheduled crawl set
- **WHEN** a scheduled refresh expands a sitemap that now contains a page absent from the catalog
- **THEN** that page is not scraped by that scheduled collection
- **AND** the set of URLs scraped is exactly the set the catalog query returned

#### Scenario: Initial ingest's crawl set is unchanged by the extraction
- **WHEN** initial collection from config runs after the refresh logic is extracted into a shared helper
- **THEN** it fetches the same pages in the same order as before, and builds the same map

### Requirement: A refresh replaces the map rather than merging into it

The system SHALL replace the lastmod map on each successful refresh, so a page that has been removed from its sitemap has no entry afterward and its stored `last_modified` returns to `NULL` on its next scheduled persist.

`NULL` means "no change signal" and is how the future skip-unchanged logic reads *unknown*
— and unknown means re-fetch, which is the safe direction. Merging into the old map would
instead preserve a timestamp no sitemap vouches for any more, and a stale-but-confident
value is what would make a later skip decision wrong rather than merely conservative.

#### Scenario: A page dropped from the sitemap loses its timestamp
- **WHEN** a page that had a `<lastmod>` is removed from the sitemap and a scheduled collection then re-persists it
- **THEN** the refreshed map has no entry for that page and its `last_modified` is written as `NULL`
