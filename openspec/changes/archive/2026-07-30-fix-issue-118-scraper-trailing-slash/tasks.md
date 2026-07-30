## 1. Red test (TDD)

- [x] 1.1 In `tests/unit/` add a test module for `_normalize_url` (instantiate the scraper or
  call the method on a minimal instance) asserting slash/no-slash collapse:
  `https://docs.rc.fas.harvard.edu/kb/x/` and `.../kb/x` return the identical string.
- [x] 1.2 Add assertions for site-root preservation (`https://host/` stays `https://host/`,
  not reduced to empty path) and empty-path input (`https://host`) not gaining/losing a
  spurious slash.
- [x] 1.3 Add assertions for query consistency (`https://host/x/?a=1` and `.../x?a=1` collapse
  to one string that still carries `a=1`).
- [x] 1.4 Add assertions for the preserved contract: empty/`None` → `None`; a schemeless
  relative URL (`/kb/x/`) does not raise.
- [x] 1.5 Run `python -m pytest tests/unit/ -k normalize_url -q` and confirm the collapse
  assertions (1.1) FAIL against the current implementation (watch it go red).

## 2. Implement the canonicalization

- [x] 2.1 In `src/data_manager/collectors/scrapers/scraper.py`, extend `_normalize_url`:
  after lowercasing scheme/netloc, compute a canonical path that strips a trailing `/`
  when `parsed.path` is longer than the root `/` (preserve root; leave empty path unchanged),
  and pass it via `parsed._replace(path=...)`. Leave query/params and the schemeless
  early-return untouched.
- [x] 2.2 Verify library behavior with `urllib.parse` if unsure (do not trust memory); keep
  the change to this single method — no unrelated reflow/churn.

## 3. Verify green + gate

- [x] 3.1 Run `python -m pytest tests/unit/ -k normalize_url -q` and confirm all new
  assertions pass (green).
- [x] 3.2 Run the full gate (`bash scripts/gate.sh`): black 24.10.0 + isort 6.0.1, unit
  tests, and diff-cover patch coverage `--fail-under=80` vs `origin/dev`. Confirm the new
  `_normalize_url` branch is covered and the gate exits 0. Never `--no-verify`.

## 4. Ship

- [x] 4.1 Commit (lowercase message, no `Co-Authored-By`) and push
  `fix/issue-118-scraper-trailing-slash`.
- [x] 4.2 Open a PR against `fasrc/archi:dev` with `closes #118`
  (`gh pr create --repo fasrc/archi --base dev`). Note in the PR body that criterion 3
  (dev redeploy `dup_groups` → 0 / ~181 page drop) is a post-merge deploy-time verification,
  not part of this PR's gate.
