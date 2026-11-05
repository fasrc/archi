## Context

Anchors are on `origin/dev` `5f3d80e9` (2026-11-05). PR #767 (fixes #459) merged on
2026-10-24 as `b8ec5e9f`, so `is_visible` exists on `dev`.

- `src/interfaces/chat_app/source_visibility.py`: `visibility_config_key(metadata)` maps the
  stored `source_type` and the `scraper` or `ticket_provider` marker to a config key.
  `is_visible(metadata, sources_config)` reads `visible` from that entry. A document with no
  key, a key that is not in the config, or an entry that is not a dict stays visible.
- The native chat: `ChatWrapper.get_top_sources` (`app.py:678`) calls
  `_get_doc_visibility` (`:819`), which returns `is_visible(metadata, self.sources_config)`.
  `self.sources_config` is `config["data_manager"]["sources"]` (`app.py:454`).
- The `/v1` endpoint gets that same `ChatWrapper`: `FlaskAppWrapper` calls
  `register_openai_compat(self.app, self.chat, ...)` (`app.py:2859`), which stores it in the
  module global `_chat_wrapper` (`openai_compat.py:77`).
- `ChatWrapper.stream` puts `raw_source_documents` and `raw_retriever_scores`
  (`app.py:2638-2645`), unfiltered, into the `final` event. `openai_compat.py` gives them to
  `format_citations` at `:414` (streaming) and `:520` (non-streaming).
- `format_citations(source_documents, scores)` pairs documents and scores by position and
  pads a short `scores` list with `-1.0` ("no score"). It reads
  `getattr(doc, "metadata", None) or {}`.
- The retriever does not read `visible` (`git grep '"visible"' -- src` finds only config
  readers and `source_visibility.py`). In both chat paths the flag filters the appended
  source list only.

## Goals / Non-Goals

**Goals:**

- A `/v1` citation block leaves out each document whose source has `visible: false`, in
  streaming and in non-streaming mode.
- A kept document keeps its own score. A kept document with no score keeps none.
- A chat wrapper with no `sources_config` dict filters nothing, so the existing tests (their
  `MagicMock` wrapper has no real config) keep their output.

**Non-Goals:**

- The model's own answer text. The model still reads a hidden document and can name or link
  it inline. The native chat has the same property. The docs say so.
- `format_citations` and `app.py`. Both stay unchanged (issue constraints).
- The non-streaming fallback arm with no `answer` field. It returns the wrapper's own
  `response` verbatim, whose source list `get_top_sources` already filtered.
- Relevance filtering. `get_top_sources` also drops documents by score; `/v1` did not copy
  that before and does not now.

## Decisions

### D1. `visible_sources` keeps each document with its own score

`visible_sources(documents, scores, sources_config) -> (documents, scores)` goes in
`source_visibility.py`, beside `is_visible`:

- If `sources_config` is not a dict, return the two arguments unchanged (the same objects).
  It calls `is_visible` for no document, so it logs nothing. A `MagicMock` config would
  otherwise log one WARNING for each document.
- Otherwise walk `documents or []` by index. Read `getattr(document, "metadata", None)`, and
  use `{}` when that is not a dict (the same tolerance as `format_citations`; `is_visible`
  calls `.get` on it). Keep the document when `is_visible` is true, and keep
  `scores[index]` only when `index < len(scores)`. `scores or []` makes `None` safe.

A missing score is always at the tail (positions at or past `len(scores)`), so a kept
document past the end keeps no score and `format_citations` pads it with `-1.0`, as before.
A score with no document is dropped. `format_citations` ignores it today, so the output
does not change; the test pins the contract.

Alternative rejected: filter only the documents and leave `scores` as it is. The scores
then shift by one position for each hidden document, and a visible document shows the
hidden document's score. The `[0.9, 0.4]` test catches it (variant M3 below).

### D2. One helper in `openai_compat.py` feeds `format_citations`

```python
def _format_visible_citations(source_documents, source_scores):
    documents, scores = visible_sources(
        source_documents,
        source_scores,
        getattr(_chat_wrapper, "sources_config", None),
    )
    return format_citations(documents, scores)
```

Both paths call it where they now call `format_citations`. Then
`grep -n "format_citations(" src/interfaces/chat_app/openai_compat.py` shows one call, and
that call takes the filtered lists. The helper reads `sources_config` at call time, so it
sees the wrapper's current value. `getattr` with a default makes a wrapper without the
attribute mean "no filter" (issue plan step 4).

Alternative rejected: filter inline at both call sites. That repeats five lines, and a
later edit can change one site and not the other.

The streaming path keeps its `if docs:` choice of the event's lists. The filter runs after
that choice, so a `final` event whose documents are all hidden gives an empty list and no
citation block.

## Tests

`tests/unit/test_source_visibility.py` (unit, task 1.1):

1. `test_visible_sources_drops_a_hidden_document_with_its_own_score`: hidden Jira then
   `git`, scores `[0.9, 0.4]`, gives `[git]` and `[0.4]`.
2. `test_visible_sources_leaves_a_missing_score_missing`: hidden, `git`, `git` with scores
   `[0.9, 0.4]` gives both `git` documents and `[0.4]`.
3. `test_visible_sources_drops_a_score_that_has_no_document`.
4. `test_visible_sources_without_a_config_dict_filters_nothing` (`None` and a `MagicMock`):
   the same objects come back and no log record is written.
5. `test_visible_sources_keeps_a_document_without_a_metadata_dict` (`metadata=None`, and an
   object with no `metadata`).
6. `test_visible_sources_with_no_documents_or_scores_returns_empty_lists` (`None`, `[]`).

`tests/unit/test_openai_compat_endpoints.py`, class `TestV1CitationVisibility` (endpoint,
task 1.2), each for `non_streaming` and `streaming`:

1. `test_v1_citations_leave_out_a_hidden_source`: the content has one `**Sources:**` block,
   no `JIRA-1`, and ``- `repo/README.md` (relevance: 0.40)``.
2. `test_v1_all_hidden_sources_give_no_citation_block`: the content is exactly
   `The answer.`.
3. `test_v1_citations_without_sources_config_keep_every_source`: `del
   chat_wrapper.sources_config`, and both documents are cited with their own scores.

## Measurement (Loop 1 scratch stage, 2026-11-05)

Loop 1 ran each stage on a scratch copy of `origin/dev` `5f3d80e9` (`git archive` of `src/`,
`tests/`, `pyproject.toml` under `/tmp`), with
`python -m pytest -q -o addopts="" <file>`, one stage at a time:

| Stage | `test_source_visibility.py` | `test_openai_compat_endpoints.py` |
| --- | --- | --- |
| `origin/dev` | `22 passed` | `27 passed` |
| Appendix A only | 1 collection error (`ImportError: cannot import name 'visible_sources'`) | — |
| A + B | `31 passed` | `27 passed` |
| A + B + C | `31 passed` | `4 failed, 29 passed` |
| A + B + C + D | `31 passed` | `33 passed` |

The 4 red items at A + B + C are `test_v1_citations_leave_out_a_hidden_source` and
`test_v1_all_hidden_sources_give_no_citation_block`, both parameters each. The 2
`test_v1_citations_without_sources_config_keep_every_source` items pass on the old code
because they are guards (variant M7).

Ten broken variants of the final code, each run on the two test files:

| Variant | Tests that fail |
| --- | --- |
| M1 streaming call unfiltered | `test_v1_citations_leave_out_a_hidden_source[streaming]`, `test_v1_all_hidden_sources_give_no_citation_block[streaming]` |
| M2 non-streaming call unfiltered | the same two, `[non_streaming]` |
| M3 scores kept by position (`scores[: len(kept)]`) | `test_visible_sources_drops_a_hidden_document_with_its_own_score`, `test_visible_sources_leaves_a_missing_score_missing`, `test_v1_citations_leave_out_a_hidden_source` (both) |
| M4 no `index < len(scores)` check | `test_visible_sources_leaves_a_missing_score_missing` |
| M5 no `sources_config` dict guard | `test_visible_sources_without_a_config_dict_filters_nothing` (both) |
| M6 no metadata dict guard | `test_visible_sources_keeps_a_document_without_a_metadata_dict` (both) |
| M7 `_chat_wrapper.sources_config` without `getattr` | `test_v1_citations_without_sources_config_keep_every_source` (both) |
| M8a `list(scores)` without `or []` | `test_visible_sources_with_no_documents_or_scores_returns_empty_lists[None]` |
| M8b `enumerate(documents)` without `or []` | `test_visible_sources_with_no_documents_or_scores_returns_empty_lists[None]` |
| M9 extra scores kept | `test_visible_sources_drops_a_score_that_has_no_document` |

Every test file that imports `openai_compat`, `source_visibility` or `citation_formatter`
(28 files) passed with A to E applied: `806 passed, 7 skipped`. `black` 24.10.0 and `isort`
6.0.1 leave every staged file unchanged. `mkdocs build --strict` passes with Appendix E;
its only link note (`services.md#service-status-board--alert-banners`) already exists on
`origin/dev`.

## Risks / Trade-offs

- [A wrapper whose `sources_config` is not a dict shows hidden sources] → It cannot happen
  in the app: `ChatWrapper.__init__` sets it from the config. "No filter" keeps today's
  output for every test double.
- [A `visible_sources` call per response] → One dict lookup per retrieved document; the
  native chat already does the same work in `get_top_sources`.
- [The model can still name a hidden source in its answer text] → Out of scope (Non-Goals),
  and the docs now say so in both pages, so a reader does not take `visible: false` as a
  retrieval filter.
