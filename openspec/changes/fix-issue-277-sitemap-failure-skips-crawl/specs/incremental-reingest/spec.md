## ADDED Requirements

### Requirement: A scheduled collection with no prior lastmod map still crawls the catalog

The system SHALL, when sitemap expansion raises `SitemapExpansionError` during a scheduled link collection and no previous refresh has ever succeeded, log a warning and still run the collection over the full catalog URL list, rather than returning without crawling.

This supersedes the no-map case of the earlier fallback requirement, which skipped the
whole pass. The skip existed to stop a mapless crawl from clearing stored `last_modified`
values through an unconditional `last_modified = EXCLUDED.last_modified` upsert. That
clause is now `COALESCE(EXCLUDED.last_modified, documents.last_modified)`
(`src/data_manager/collectors/utils/catalog_postgres.py`), so the database preserves a
stored value whenever the incoming one is absent, and the hazard the skip defended against
cannot occur. The URL list passed to the collection SHALL be the same catalog list the
non-degraded path passes, filtered only for empty values — a failed sitemap expansion SHALL
NOT narrow which pages are crawled.

#### Scenario: Expansion fails with no map ever built and the crawl still runs
- **WHEN** `SitemapExpansionError` is raised during a scheduled link collection and no refresh has previously succeeded
- **THEN** the collection runs with the same catalog URL list it would receive on a successful pass
- **AND** a warning records that the pass is degraded, naming the failed expansion and that pages new in this pass carry no timestamp
- **AND** the scheduled pass returns normally, so the caller's success bookkeeping is unchanged

#### Scenario: A degraded pass is not reported as a hard error
- **WHEN** a scheduled pass crawls without a lastmod map
- **THEN** the handler emits exactly one warning for that pass and no error-level log, because a pass that crawls with fewer new stamps is degraded and not failed

### Requirement: A mapless scheduled pass preserves stored last_modified values

The system SHALL leave every stored `documents.last_modified` value unchanged during a scheduled pass that runs without a lastmod map, because the pass supplies no incoming timestamp and the catalog upsert preserves the stored value when the incoming one is absent.

The guarantee is a property of two layers and SHALL be pinned at both. The scrape path
attaches `last_modified` to a resource's metadata only while the map is non-empty, so a
mapless pass carries no timestamp into persistence. The catalog upsert resolves the
conflict path with `COALESCE(EXCLUDED.last_modified, documents.last_modified)` and still
binds the absent value as `NULL`, so preservation is decided by the database rather than by
a caller omitting a parameter. A test that replaces the upsert with a mock does not satisfy
this requirement, because the hazard it guards against lived in the SQL a mock hides.

#### Scenario: A mapless pass attaches no timestamp to a scraped resource
- **WHEN** a page is scraped and persisted while the sitemap lastmod map is empty
- **THEN** the resource's metadata carries no `last_modified` key

#### Scenario: The upsert preserves the stored value when the pass supplies none
- **WHEN** a page already holding a stored `last_modified` is re-persisted with no `last_modified` in its metadata
- **THEN** the statement resolves the conflict with `COALESCE(EXCLUDED.last_modified, documents.last_modified)`
- **AND** the absent value is still bound as `NULL` in the `last_modified` slot, so the stored timestamp survives by the database's decision

#### Scenario: New pages in a mapless pass are stored without a timestamp
- **WHEN** a page first seen during a mapless scheduled pass is inserted
- **THEN** its `last_modified` is `NULL` and the pass completes, and a later pass with a working map supplies the timestamp
