## ADDED Requirements

### Requirement: Staging skips an input list it cannot copy
`TemplateManager._copy_web_input_lists` SHALL log a WARNING that contains the configured path and continue with the next list when copying a configured input list raises `OSError`; it SHALL NOT raise.

#### Scenario: Permission-denied list next to a good list
- **WHEN** the configured input lists are a mode-000 file and a readable file, and `_copy_web_input_lists` runs as a non-root user
- **THEN** no exception is raised, only the readable list is present in `<base_dir>/weblists`, and a WARNING record contains the mode-000 file's path

### Requirement: The runtime reader skips an input list it cannot read
`ScraperManager._collect_urls_from_lists` SHALL log a WARNING that contains the staged path, set `_input_lists_complete` to `False`, and continue with the next list when reading a staged input list raises `OSError` or `UnicodeDecodeError`; it SHALL NOT raise.

#### Scenario: Non-UTF-8 list next to a good list
- **WHEN** `weblists/` holds a list with the bytes `b"\xff\xfe\x00git-https://x\n"` and a list with `https://x.example.edu/one`, and `_collect_urls_from_lists` reads both
- **THEN** no exception is raised, the result is `["https://x.example.edu/one"]`, `_input_lists_complete` is `False`, and a WARNING record contains the non-UTF-8 list's staged path

#### Scenario: Permission-denied staged list next to a good list
- **WHEN** `weblists/` holds a mode-000 list and a readable list, and `_collect_urls_from_lists` runs as a non-root user
- **THEN** no exception is raised, the result holds only the readable list's URLs, `_input_lists_complete` is `False`, and a WARNING record contains the mode-000 list's staged path
