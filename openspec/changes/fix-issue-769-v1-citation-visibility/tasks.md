## 1. Filter hidden sources out of /v1 citations (fasrc/archi#769)

- [ ] 1.1 Red then green in one task (do not commit a red state). Write Appendix A to `/tmp/769-A.diff` with the Write tool and run `git apply -p1 /tmp/769-A.diff` from the repository root. It changes the import block of `tests/unit/test_source_visibility.py` and adds 6 test functions (9 test items) and one helper AFTER the file's current last line `    assert result is True`. Run `python -m pytest tests/unit/test_source_visibility.py -q`. Expect exactly 1 collection error: `ImportError: cannot import name 'visible_sources'`. Then write Appendix B to `/tmp/769-B.diff` and `git apply -p1` it. It adds `visible_sources` to `src/interfaces/chat_app/source_visibility.py` (design D1). Re-run the file: expect exactly `31 passed` (22 before). If a total differs, read the failure and write what you saw under "## Measurement" in `design.md` before you change anything. Run `bash scripts/gate.sh` in the foreground, bare (no pipe, no redirect, never in the background), and commit only when it is green (message: `filter hidden sources with their scores`).
- [ ] 1.2 Red then green in one task (do not commit a red state). Write Appendix C to `/tmp/769-C.diff` and `git apply -p1` it. It adds 4 module helpers and the class `TestV1CitationVisibility` (3 test functions, 6 test items) AFTER the current last line of `tests/unit/test_openai_compat_endpoints.py`, `        assert data["archi_message_id"] is None`; that line must stay the last line of `test_string_message_id_returns_null`. Run `python -m pytest tests/unit/test_openai_compat_endpoints.py -q`. Expect exactly `4 failed, 29 passed`: `test_v1_citations_leave_out_a_hidden_source` and `test_v1_all_hidden_sources_give_no_citation_block`, both `[non_streaming]` and `[streaming]`, each because `JIRA-1` is in the citations. Then write Appendix D to `/tmp/769-D.diff` and `git apply -p1` it. It adds `_format_visible_citations` to `src/interfaces/chat_app/openai_compat.py` and calls it from `_streaming_response` and `_non_streaming_response` (design D2). Re-run the file: expect exactly `33 passed`. Then check: `grep -n "format_citations(" src/interfaces/chat_app/openai_compat.py` shows one call, inside `_format_visible_citations`; `git diff origin/dev -- src/archi/utils/citation_formatter.py src/interfaces/chat_app/app.py` prints nothing; and for each of the two test files, `grep -E '^ *def test_' <file> | sed 's/(.*//' | sort | uniq -d | wc -l` prints `0`. Run `bash scripts/gate.sh` in the foreground, bare, and commit when green (message: `apply source visibility to v1 citations`).

## 2. Docs

- [ ] 2.1 Write Appendix E to `/tmp/769-E.diff` and `git apply -p1` it. It changes the `visible` paragraph and the one-line `visible` note in `docs/docs/configuration.md`, and the "Source Citations" section of `docs/docs/api-reference-v1.md` (one new paragraph and one changed sentence). Change no other text. Run `bash scripts/gate.sh` in the foreground, bare, and commit when green (message: `document v1 citation visibility`).

## 3. Publish

- [ ] 3.1 Tick this box, commit the tick through the hook (message: `mark #769 tasks complete`), then push and open the PR, so the push carries every commit. Push with `git push -u origin fix/issue-769-v1-citation-visibility`. Write the PR body to a file under `/tmp` (never under the repository) and open the PR with `gh pr create --repo fasrc/archi --base dev --head fix/issue-769-v1-citation-visibility --title "fix(v1): leave hidden sources out of /v1 citations" --body-file <that file>`. Put `Closes #769` in the PR **body**, not the title. In the body: state the defect (the `/v1` endpoint gave the unfiltered retriever documents to `format_citations`, so a `visible: false` source was cited to every `/v1` client, the Slack bot too); name `visible_sources` and `_format_visible_citations`; state that `format_citations` and `app.py` are unchanged; copy the stage table and the broken-variant table from `design.md` "## Measurement"; name the two docs pages; and add one line: "Non-goal (issue #769): the model still reads a hidden document, so its own answer text can still name it; this change filters the appended citation block only." No `Co-Authored-By` trailer. STOP at the open PR; do not merge.

## Appendix (Loop 1 scratch evidence, 2026-11-05)

Loop 1 made these patches on a scratch copy of `origin/dev` `5f3d80e9`, and ran every stage
in design.md "## Measurement" on them. Patches A to E apply in order with `git apply -p1` on
`origin/dev` `5f3d80e9`, and the result is byte-identical to the tested files. `black`
24.10.0 and `isort` 6.0.1 leave them unchanged. Copy the patches; do not rewrite them. A
blank context line in a patch is an empty line; `git apply` accepts it.

### Appendix A (task 1.1, tests)

````diff
diff --git a/tests/unit/test_source_visibility.py b/tests/unit/test_source_visibility.py
--- a/tests/unit/test_source_visibility.py
+++ b/tests/unit/test_source_visibility.py
@@ -1,8 +1,14 @@
 import logging
+from types import SimpleNamespace
+from unittest.mock import MagicMock

 import pytest

-from src.interfaces.chat_app.source_visibility import is_visible, visibility_config_key
+from src.interfaces.chat_app.source_visibility import (
+    is_visible,
+    visibility_config_key,
+    visible_sources,
+)


 @pytest.mark.parametrize(
@@ -74,3 +80,71 @@ def test_non_dict_entry_visible_no_raise():
     config = {"links": None}
     result = is_visible({"source_type": "web"}, config)
     assert result is True
+
+
+# --- visible_sources: the /v1 citation filter (issue #769) ---------------------------
+
+_HIDE_JIRA = {"jira": {"visible": False}, "git": {"visible": True}}
+
+
+def _visibility_doc(**metadata):
+    return SimpleNamespace(metadata=metadata)
+
+
+def test_visible_sources_drops_a_hidden_document_with_its_own_score():
+    hidden = _visibility_doc(source_type="ticket", ticket_provider="jira")
+    shown = _visibility_doc(source_type="git")
+
+    documents, scores = visible_sources([hidden, shown], [0.9, 0.4], _HIDE_JIRA)
+
+    assert documents == [shown]
+    assert scores == [0.4]
+
+
+def test_visible_sources_leaves_a_missing_score_missing():
+    hidden = _visibility_doc(source_type="ticket", ticket_provider="jira")
+    first = _visibility_doc(source_type="git")
+    second = _visibility_doc(source_type="git")
+
+    documents, scores = visible_sources([hidden, first, second], [0.9, 0.4], _HIDE_JIRA)
+
+    assert documents == [first, second]
+    assert scores == [0.4]
+
+
+def test_visible_sources_drops_a_score_that_has_no_document():
+    shown = _visibility_doc(source_type="git")
+
+    documents, scores = visible_sources([shown], [0.9, 0.4], _HIDE_JIRA)
+
+    assert documents == [shown]
+    assert scores == [0.9]
+
+
+@pytest.mark.parametrize("sources_config", [None, MagicMock()])
+def test_visible_sources_without_a_config_dict_filters_nothing(sources_config, caplog):
+    hidden = _visibility_doc(source_type="ticket", ticket_provider="jira")
+    input_documents = [hidden]
+    input_scores = [0.9]
+
+    with caplog.at_level(logging.DEBUG):
+        documents, scores = visible_sources(
+            input_documents, input_scores, sources_config
+        )
+
+    assert documents is input_documents
+    assert scores is input_scores
+    assert caplog.records == []
+
+
+@pytest.mark.parametrize("document", [SimpleNamespace(metadata=None), object()])
+def test_visible_sources_keeps_a_document_without_a_metadata_dict(document):
+    documents, scores = visible_sources([document], [0.7], _HIDE_JIRA)
+
+    assert documents == [document]
+    assert scores == [0.7]
+
+
+@pytest.mark.parametrize("empty", [None, []])
+def test_visible_sources_with_no_documents_or_scores_returns_empty_lists(empty):
+    assert visible_sources(empty, empty, _HIDE_JIRA) == ([], [])
````

### Appendix B (task 1.1, code)

````diff
diff --git a/src/interfaces/chat_app/source_visibility.py b/src/interfaces/chat_app/source_visibility.py
--- a/src/interfaces/chat_app/source_visibility.py
+++ b/src/interfaces/chat_app/source_visibility.py
@@ -43,3 +43,28 @@ def is_visible(metadata, sources_config) -> bool:
         logger.warning("sources_config[%r] is not a dict; treating as visible", key)
         return True
     return bool(entry.get("visible", True))
+
+
+def visible_sources(documents, scores, sources_config):
+    """Drop each document that ``is_visible`` hides, together with its own score.
+
+    ``scores`` pairs with ``documents`` by position, the way ``format_citations``
+    reads them. A kept document that has no score keeps none, so the formatter still
+    pads it with ``-1.0``. A ``sources_config`` that is not a dict means no filter:
+    the two arguments come back unchanged (issue #769).
+    """
+    if not isinstance(sources_config, dict):
+        return documents, scores
+    scores = list(scores or [])
+    kept_documents = []
+    kept_scores = []
+    for index, document in enumerate(documents or []):
+        metadata = getattr(document, "metadata", None)
+        if not isinstance(metadata, dict):
+            metadata = {}
+        if not is_visible(metadata, sources_config):
+            continue
+        kept_documents.append(document)
+        if index < len(scores):
+            kept_scores.append(scores[index])
+    return kept_documents, kept_scores
````

### Appendix C (task 1.2, tests)

````diff
diff --git a/tests/unit/test_openai_compat_endpoints.py b/tests/unit/test_openai_compat_endpoints.py
--- a/tests/unit/test_openai_compat_endpoints.py
+++ b/tests/unit/test_openai_compat_endpoints.py
@@ -942,3 +942,114 @@ class TestArchMessageId:
         assert resp.status_code == 200
         data = resp.get_json()
         assert data["archi_message_id"] is None
+
+
+# ---------------------------------------------------------------------------
+# Source visibility in /v1 citations (issue #769)
+# ---------------------------------------------------------------------------
+
+_V1_HIDE_JIRA = {"jira": {"visible": False}, "git": {"visible": True}}
+
+
+def _make_visibility_source_doc(display_name, **metadata):
+    """Create a fake source document whose metadata names its source."""
+    doc = MagicMock()
+    doc.metadata = {"display_name": display_name, **metadata}
+    return doc
+
+
+def _visibility_final_events(documents, scores):
+    return [
+        {"type": "chunk", "content": "The answer."},
+        {
+            "type": "final",
+            "answer": "The answer.",
+            "response": "The answer.",
+            "source_documents": documents,
+            "retriever_scores": scores,
+        },
+    ]
+
+
+def _hidden_jira_and_visible_git_docs():
+    return [
+        _make_visibility_source_doc(
+            "JIRA-1", source_type="ticket", ticket_provider="jira"
+        ),
+        _make_visibility_source_doc("repo/README.md", source_type="git"),
+    ]
+
+
+def _v1_content(app, stream):
+    """POST one /v1 request and return the assistant text, joined from SSE if streamed."""
+    resp = app.test_client().post(
+        "/v1/chat/completions",
+        json={
+            "model": "test-model",
+            "messages": [{"role": "user", "content": "hi"}],
+            "stream": stream,
+        },
+    )
+    assert resp.status_code == 200
+    if not stream:
+        return resp.get_json()["choices"][0]["message"]["content"]
+    content = ""
+    for line in resp.get_data(as_text=True).split("\n"):
+        if line.startswith("data: ") and line != "data: [DONE]":
+            delta = json.loads(line[len("data: ") :])["choices"][0]["delta"]
+            content += delta.get("content", "")
+    return content
+
+
+class TestV1CitationVisibility:
+
+    @pytest.mark.parametrize(
+        "stream", [False, True], ids=["non_streaming", "streaming"]
+    )
+    def test_v1_citations_leave_out_a_hidden_source(self, app_no_auth, stream):
+        app, chat_wrapper = app_no_auth
+        chat_wrapper.sources_config = _V1_HIDE_JIRA
+        chat_wrapper.stream.return_value = iter(
+            _visibility_final_events(_hidden_jira_and_visible_git_docs(), [0.9, 0.4])
+        )
+
+        content = _v1_content(app, stream)
+
+        assert content.startswith("The answer.")
+        assert content.count("**Sources:**") == 1
+        assert "JIRA-1" not in content
+        assert "- `repo/README.md` (relevance: 0.40)" in content
+
+    @pytest.mark.parametrize(
+        "stream", [False, True], ids=["non_streaming", "streaming"]
+    )
+    def test_v1_all_hidden_sources_give_no_citation_block(self, app_no_auth, stream):
+        app, chat_wrapper = app_no_auth
+        chat_wrapper.sources_config = _V1_HIDE_JIRA
+        hidden = _make_visibility_source_doc(
+            "JIRA-1", source_type="ticket", ticket_provider="jira"
+        )
+        chat_wrapper.stream.return_value = iter(
+            _visibility_final_events([hidden], [0.9])
+        )
+
+        content = _v1_content(app, stream)
+
+        assert content == "The answer."
+
+    @pytest.mark.parametrize(
+        "stream", [False, True], ids=["non_streaming", "streaming"]
+    )
+    def test_v1_citations_without_sources_config_keep_every_source(
+        self, app_no_auth, stream
+    ):
+        app, chat_wrapper = app_no_auth
+        del chat_wrapper.sources_config
+        chat_wrapper.stream.return_value = iter(
+            _visibility_final_events(_hidden_jira_and_visible_git_docs(), [0.9, 0.4])
+        )
+
+        content = _v1_content(app, stream)
+
+        assert "- `JIRA-1` (relevance: 0.90)" in content
+        assert "- `repo/README.md` (relevance: 0.40)" in content
````

### Appendix D (task 1.2, code)

````diff
diff --git a/src/interfaces/chat_app/openai_compat.py b/src/interfaces/chat_app/openai_compat.py
--- a/src/interfaces/chat_app/openai_compat.py
+++ b/src/interfaces/chat_app/openai_compat.py
@@ -20,6 +20,7 @@ import psycopg2
 from flask import Blueprint, Response, g, jsonify, request, stream_with_context

 from src.archi.utils.citation_formatter import format_citations
+from src.interfaces.chat_app.source_visibility import visible_sources
 from src.interfaces.slack_trace_tags import (
     slack_trace_attributes,
     tag_current_span,
@@ -378,6 +379,21 @@ def _extract_delta(content, accumulated):
     return content, accumulated + content


+def _format_visible_citations(source_documents, source_scores):
+    """Format the citation block from the sources the config lets a reader see.
+
+    The native chat drops a document from ``visible: false`` sources before it
+    lists sources (``get_top_sources``); /v1 drops it here, with its own score
+    (issue #769). A chat wrapper without ``sources_config`` filters nothing.
+    """
+    documents, scores = visible_sources(
+        source_documents,
+        source_scores,
+        getattr(_chat_wrapper, "sources_config", None),
+    )
+    return format_citations(documents, scores)
+
+
 # ---------------------------------------------------------------------------
 # Streaming response
 # ---------------------------------------------------------------------------
@@ -411,7 +427,9 @@ def _streaming_response(request_id, model, stream_kwargs, trace_attributes=None)
                         source_documents = docs
                         source_scores = scores_list

-                    citation_text = format_citations(source_documents, source_scores)
+                    citation_text = _format_visible_citations(
+                        source_documents, source_scores
+                    )
                     if citation_text:
                         accumulated[0] += citation_text
                         yield _sse_chunk(request_id, model, content=citation_text)
@@ -517,7 +535,7 @@ def _non_streaming_response(request_id, model, stream_kwargs, trace_attributes=N
         )

     if append_citations:
-        citation_text = format_citations(source_documents, source_scores)
+        citation_text = _format_visible_citations(source_documents, source_scores)
         if citation_text:
             final_content += citation_text

````

### Appendix E (task 2.1, docs)

````diff
diff --git a/docs/docs/api-reference-v1.md b/docs/docs/api-reference-v1.md
--- a/docs/docs/api-reference-v1.md
+++ b/docs/docs/api-reference-v1.md
@@ -184,6 +184,11 @@ pipeline returned without a score are listed last, without a `(relevance: ...)`

 Collection labels appear only when sources span multiple collections.

+The block leaves out each document whose source has `visible: false` in
+`data_manager.sources`, with its score, in streaming and non-streaming responses. This is the
+rule the chat UI's own source list obeys (see [Configuration](configuration.md)). The model
+still reads a hidden document, so the model's own answer text can still name it.
+
 A `/v1` response carries **at most one** source list appended by archi — the endpoint's citation
 block or the native chat UI's, never both. A source section the model wrote into its own answer
 text is passed through as written; the endpoint does not detect or remove it.
@@ -200,5 +205,6 @@ with one citation block appended when the `final` event arrives, whether or not
 `answer`, and the chat UI's own list is never involved.

 Either path can come back with no source list at all, even when retrieval found documents: the
-citation block is empty when no document carries a usable display name, and the chat UI's list is
-empty when its own relevance and visibility filtering leaves nothing to show.
+citation block is empty when no document carries a usable display name or every document comes
+from a hidden source, and the chat UI's list is empty when its own relevance and visibility
+filtering leaves nothing to show.
diff --git a/docs/docs/configuration.md b/docs/docs/configuration.md
--- a/docs/docs/configuration.md
+++ b/docs/docs/configuration.md
@@ -193,8 +193,11 @@ these to turn anything off or to hide anything:
 | `data_manager.sources.links.html_scraper.reset_data` | no consumer reads it — `ScraperManager` extracts the `html_scraper` block but reads only `verify_urls` and `enable_warnings` when it builds link scrapers |
 | `services.chat_app.flask_debug_mode` | the chat service runs on gunicorn and always turns the Flask debugger off. The grader and the data manager still read their own `flask_debug_mode` keys |

-`visible: false` works for all sources. The chat app maps each document's stored metadata
-to a config key using the following table, then reads `visible` from that config entry:
+`visible: false` works for all sources. It removes a document from the chat app's source list
+and from the citation block of `/v1/chat/completions`, streaming and non-streaming. It does not
+remove the document from retrieval: the model still reads it, and the model's own answer text
+can still name it. Both lists map each document's stored metadata to a config key using the
+following table, then read `visible` from that config entry:

 | Stored `source_type` | Stored marker | Config key used |
 |---|---|---|
@@ -740,7 +743,7 @@ data_manager:
       anonymize_data: true
 ```

-The `visible` flag on any source (`sources.<name>.visible`) controls whether content appears in chat citations (default: `true`).
+The `visible` flag on any source (`sources.<name>.visible`) controls whether content appears in chat citations and in `/v1` citations (default: `true`).

 ### Processing (HTML → Markdown & categorization)

````
