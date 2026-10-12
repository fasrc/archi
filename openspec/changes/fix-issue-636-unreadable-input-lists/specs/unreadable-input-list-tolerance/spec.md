## ADDED Requirements

### Requirement: Staging skips an input list it cannot copy
`TemplateManager._copy_web_input_lists` SHALL log a WARNING that contains the configured path and continue with the next list when copying a configured input list raises `OSError`; it SHALL NOT raise, and it SHALL NOT leave a partial copy of that list in `weblists`.

#### Scenario: Permission-denied list next to a good list
- **WHEN** the configured input lists are a mode-000 file and a readable file, and `_copy_web_input_lists` runs as a non-root user
- **THEN** no exception is raised, only the readable list is present in `<base_dir>/weblists`, and a WARNING record contains the mode-000 file's path

#### Scenario: A copy that fails part-way
- **WHEN** copying a configured list writes part of the destination and then raises `OSError`
- **THEN** no exception is raised, no file for that list is present in `<base_dir>/weblists`, the other list is staged, and a WARNING record contains the failed list's path

#### Scenario: The later of two same-named lists fails to copy
- **WHEN** two configured lists share a basename, the lexically earlier one is copied, and copying the lexically later one raises `OSError`
- **THEN** no file with that basename is present in `<base_dir>/weblists`, because source inference treats the later list as the one staged

### Requirement: The runtime reader skips an input list it cannot read
`ScraperManager._collect_urls_from_lists` SHALL log a WARNING that contains the staged path, set `_input_lists_complete` to `False`, and continue with the next list when reading a staged input list raises `OSError` or `UnicodeDecodeError`; it SHALL NOT raise.

#### Scenario: Non-UTF-8 list next to a good list
- **WHEN** `weblists/` holds a list with the bytes `b"\xff\xfe\x00git-https://x\n"` and a list with `https://x.example.edu/one`, and `_collect_urls_from_lists` reads both
- **THEN** no exception is raised, the result is `["https://x.example.edu/one"]`, `_input_lists_complete` is `False`, and a WARNING record contains the non-UTF-8 list's staged path

#### Scenario: Permission-denied staged list next to a good list
- **WHEN** `weblists/` holds a mode-000 list and a readable list, and `_collect_urls_from_lists` runs as a non-root user
- **THEN** no exception is raised, the result holds only the readable list's URLs, `_input_lists_complete` is `False`, and a WARNING record contains the mode-000 list's staged path

### Requirement: An unreadable input list keeps the sitemap lastmod map
`ScraperManager._refresh_sitemap_lastmod_map` SHALL treat the refresh as incomplete when `_input_lists_complete` is `False`, even when the readable lists still yield sitemap sources: it SHALL retain a previously validated map and SHALL NOT set `_sitemap_map_valid`.

#### Scenario: One list unreadable, another still lists a sitemap
- **WHEN** a valid map exists, one configured list cannot be read, and a scheduled refresh expands the sitemap from the readable list
- **THEN** the previous map is kept unchanged

#### Scenario: A second full ingest with one list unreadable
- **WHEN** `collect_all_from_config` built a valid map, one configured list cannot be read, and `collect_all_from_config` runs again on the same manager
- **THEN** the previous map is kept unchanged, because the full ingest does not clear the map before the refresh when sitemap sources are present

#### Scenario: No valid map yet
- **WHEN** no valid map exists and a refresh runs with `_input_lists_complete` set to `False`
- **THEN** `_sitemap_map_valid` stays `False`
