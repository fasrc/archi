## ADDED Requirements

### Requirement: GitScraper.collect SHALL isolate a per-repository harvest failure

When harvesting one repository raises an `Exception`, `GitScraper.collect` MUST log an ERROR that names the repository URL, record `(repo_name, str(exc))` in `last_failures`, remove that repository's clone directory, and continue with the next URL. The resources harvested from the other repositories MUST be returned.

#### Scenario: First repository fails to harvest, second succeeds

- **WHEN** `collect([url_a, url_b])` runs, both clones succeed, harvesting `url_a` raises `OSError("boom")`, and harvesting `url_b` returns one resource
- **THEN** `collect` does not raise
- **AND** it returns exactly the resource from `url_b`
- **AND** an ERROR log record names `url_a`
- **AND** `last_failures` equals `[(<repo name of url_a>, "boom")]`

#### Scenario: Clone directory of the failed repository is removed

- **WHEN** harvesting a repository raises an exception
- **THEN** the clone directory of that repository does not exist after `collect` returns

### Requirement: A harvest failure SHALL NOT catch BaseException

`GitScraper.collect` MUST NOT catch exceptions that are not subclasses of `Exception`, such as `KeyboardInterrupt`; these MUST still go out of `collect`.

#### Scenario: KeyboardInterrupt during harvest

- **WHEN** harvesting a repository raises `KeyboardInterrupt`
- **THEN** `collect` raises `KeyboardInterrupt`
- **AND** the clone directory of that repository is removed
