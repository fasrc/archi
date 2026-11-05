## 1. Refuse a bad chunk size (fasrc/archi#778)

- [x] 1.1 Red then green in one task (do not commit a red state). Write Appendix A to
      `/tmp/778-A.diff` with the Write tool and run `git apply -p1 /tmp/778-A.diff` from the
      repository root. It appends 4 test functions at the end of
      `tests/unit/test_vectorstore_manager_hierarchical.py` and changes
      `_resolve_chunk_sizes` in `src/data_manager/vectorstore/manager.py` (design D1). To see
      the red step first, apply the patch, then run
      `git stash push src/data_manager/vectorstore/manager.py`, run
      `python -m pytest -q tests/unit/test_vectorstore_manager_hierarchical.py tests/unit/test_ingest_provenance.py`
      and confirm EXACTLY `14 failed, 102 passed`, then `git stash pop` and confirm EXACTLY
      `116 passed`. If a count differs, STOP and write what you saw under "## Measurement" in
      `design.md`. Run `bash scripts/gate.sh` in the foreground and wait for exit 0. Commit
      only when it is green (message: `refuse a chunk size that is not a positive int`).

- [ ] 1.2 Red then green in one task (do not commit a red state). Write Appendix B to
      `/tmp/778-B.diff` and run `git apply -p1 /tmp/778-B.diff`. It appends 3 test functions
      at the end of `tests/unit/test_ingest_provenance.py` and maps a `None` size to its
      default in `effective_chunking` (`src/utils/ingest_provenance.py`, design D2). To see
      the red step first, apply the patch, run `git stash push src/utils/ingest_provenance.py`,
      run the same pytest command as in 1.1 and confirm EXACTLY `4 failed, 116 passed`, then
      `git stash pop` and confirm EXACTLY `120 passed`. If a count differs, STOP and write it
      under "## Measurement" in `design.md`. Run `bash scripts/gate.sh` in the foreground and
      wait for exit 0. Commit when green (message: `report the default for an empty chunk
      size in provenance`).

## 2. Docs

- [ ] 2.1 Write Appendix C to `/tmp/778-C.diff` and run `git apply -p1 /tmp/778-C.diff`. It
      replaces the last sentence of the "Backward compatibility" note in the `chunking`
      section of `docs/docs/configuration.md` (the #596 sentence that ends "the hierarchical
      parser cannot use it."). Change nothing else in the file. Run `bash scripts/gate.sh`
      and commit when green (message: `document the chunk size refusal`).

## 3. Publish

- [ ] 3.1 Tick this box, commit the tick through the hook (message: `mark #778 tasks
      complete`), then push and open the PR, so the push carries every commit. Push with
      `git push -u origin fix/issue-778-chunk-size-validation`. Open the PR with
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-778-chunk-size-validation`,
      the title `fix(vectorstore): refuse a chunk size that is not a positive int`, and
      `Closes #778` in the BODY (not the title). Pass the body with `--body` or a file under
      `/tmp`; do NOT commit a PR-body file to the repository. In the body, copy the
      "## Measurement" tables of `design.md`, and add one line: "Runtime effect: the data
      manager and the chat service now stop at startup on a bad size, the same as on a bad
      `chunk_overlap`." STOP at the open PR — do not merge.

## Appendix (Loop 1 scratch evidence, 2026-11-05)

Loop 1 made these patches on a scratch copy of `origin/dev` `5f3d80e9`, and ran every stage
in design.md "## Measurement" on them. Patches A, B, and C apply in order with
`git apply -p1` on `origin/dev` `5f3d80e9`, and the result is byte-identical to the tested
files. `black` 24.10.0 and `isort` 6.0.1 leave them unchanged. Copy the patches; do not
rewrite them. A blank context line in a patch is an empty line; `git apply` accepts it.
Before you apply a test patch, read its last hunk: the new tests go after the last line of
the file, and no existing `assert` line may sit below the inserted block.

### Appendix A (task 1.1, tests and `manager.py`)

````diff
diff --git a/src/data_manager/vectorstore/manager.py b/src/data_manager/vectorstore/manager.py
index a32b17a..311a942 100644
--- a/src/data_manager/vectorstore/manager.py
+++ b/src/data_manager/vectorstore/manager.py
@@ -50,16 +50,39 @@ def _report_embedding_progress(embedding_progress, done, total):
 SUPPORTED_DISTANCE_METRICS = ["l2", "cosine", "ip"]


+def _resolve_chunk_size(chunking_cfg, key, default):
+    """Resolve one hierarchical chunk size, refusing a value the parser cannot use.
+
+    ``None`` (key present but empty in YAML) resolves to ``default``, matching the
+    absent-key behavior. A bool, a non-integer, or a value below 1 raises
+    ``ValueError`` naming the key, so the operator sees one clear error at
+    construction time rather than a per-document ``TypeError`` during ingest.
+    """
+    value = chunking_cfg.get(key, default)
+    if value is None:
+        return default
+    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
+        raise ValueError(
+            f"data_manager.chunking.{key} must be a positive int, got {value!r}"
+        )
+    return value
+
+
 def _resolve_chunk_sizes(chunking_cfg):
     """Resolve hierarchical parent/child target sizes from ``data_manager.chunking``.

     Reads ``parent_chunk_size`` / ``child_chunk_size`` from the chunking config
     section, falling back independently to the node-parser defaults when a key is
-    absent. Keeping this resolution backward-compatible means an existing
-    deployment that omits the keys reproduces its prior chunking exactly.
+    absent or empty. Keeping this resolution backward-compatible means an existing
+    deployment that omits the keys reproduces its prior chunking exactly. A size
+    that is not a positive int raises ``ValueError`` (see ``_resolve_chunk_size``).
     """
-    parent = chunking_cfg.get("parent_chunk_size", DEFAULT_PARENT_CHUNK_SIZE)
-    child = chunking_cfg.get("child_chunk_size", DEFAULT_CHILD_CHUNK_SIZE)
+    parent = _resolve_chunk_size(
+        chunking_cfg, "parent_chunk_size", DEFAULT_PARENT_CHUNK_SIZE
+    )
+    child = _resolve_chunk_size(
+        chunking_cfg, "child_chunk_size", DEFAULT_CHILD_CHUNK_SIZE
+    )
     return parent, child


diff --git a/tests/unit/test_vectorstore_manager_hierarchical.py b/tests/unit/test_vectorstore_manager_hierarchical.py
index 9832551..327479d 100644
--- a/tests/unit/test_vectorstore_manager_hierarchical.py
+++ b/tests/unit/test_vectorstore_manager_hierarchical.py
@@ -1489,3 +1489,67 @@ def test_add_to_postgres_hierarchical_count_excludes_rolled_back_deletes(
         if "unreferenced parent nodes after re-ingest" in r.message
     ]
     assert summary == ["Deleted 0 unreferenced parent nodes after re-ingest"]
+
+
+# ---------------------------------------------------------------------------
+# _resolve_chunk_sizes validation — issue #778
+# ---------------------------------------------------------------------------
+
+
+@pytest.mark.parametrize("key", ["parent_chunk_size", "child_chunk_size"])
+@pytest.mark.parametrize("bad", ["512", True, False, 0, -1, 2.5])
+def test_resolve_chunk_sizes_rejects_a_size_that_is_not_a_positive_int(key, bad):
+    with pytest.raises(ValueError) as excinfo:
+        _resolve_chunk_sizes({key: bad})
+    message = str(excinfo.value)
+    assert f"data_manager.chunking.{key} " in message
+    assert message.endswith(f"got {bad!r}")
+
+
+def test_resolve_chunk_sizes_null_resolves_each_size_to_its_own_default():
+    assert _resolve_chunk_sizes(
+        {"parent_chunk_size": None, "child_chunk_size": None}
+    ) == (DEFAULT_PARENT_CHUNK_SIZE, DEFAULT_CHILD_CHUNK_SIZE)
+    assert _resolve_chunk_sizes(
+        {"parent_chunk_size": None, "child_chunk_size": 256}
+    ) == (DEFAULT_PARENT_CHUNK_SIZE, 256)
+    assert _resolve_chunk_sizes(
+        {"parent_chunk_size": 1024, "child_chunk_size": None}
+    ) == (1024, DEFAULT_CHILD_CHUNK_SIZE)
+
+
+def test_resolve_chunk_sizes_accepts_a_size_of_one():
+    assert _resolve_chunk_sizes({"parent_chunk_size": 1, "child_chunk_size": 1}) == (
+        1,
+        1,
+    )
+
+
+def test_vectorstore_manager_refuses_a_quoted_chunk_size_when_built(monkeypatch):
+    """The constructor refuses the size, not the first document of an ingest."""
+    from src.utils.config_service import ConfigService
+
+    monkeypatch.setattr(
+        manager_module, "PostgresCatalogService", lambda *args, **kwargs: MagicMock()
+    )
+    monkeypatch.setattr(
+        ConfigService,
+        "_resolve_embedding_classes",
+        staticmethod(lambda class_map: class_map),
+    )
+    config = {
+        "data_manager": {
+            "embedding_name": "FakeEmbeddings",
+            "collection_name": "issue_778",
+            "distance_metric": "cosine",
+            "embedding_class_map": {"FakeEmbeddings": {"class": MagicMock}},
+            "chunk_size": 1000,
+            "chunk_overlap": 150,
+            "chunking": {"strategy": "sentence", "child_chunk_size": "512"},
+        }
+    }
+
+    with pytest.raises(ValueError, match="data_manager.chunking.child_chunk_size"):
+        VectorStoreManager(
+            config=config, global_config={}, data_path="/tmp", pg_config={}
+        )
````

### Appendix B (task 1.2, tests and `ingest_provenance.py`)

````diff
diff --git a/src/utils/ingest_provenance.py b/src/utils/ingest_provenance.py
index e1f68e5..4c960b2 100644
--- a/src/utils/ingest_provenance.py
+++ b/src/utils/ingest_provenance.py
@@ -103,8 +103,12 @@ def effective_chunking(data_manager_config: Any) -> Dict[str, Any]:
     strategy = chunking.get("strategy", _DEFAULT_CHUNKING_STRATEGY)

     if strategy in (_DEFAULT_CHUNKING_STRATEGY, _MARKDOWN_STRATEGY):
-        parent = chunking.get("parent_chunk_size", _DEFAULT_PARENT_CHUNK_SIZE)
-        child = chunking.get("child_chunk_size", _DEFAULT_CHILD_CHUNK_SIZE)
+        parent = chunking.get("parent_chunk_size")
+        if parent is None:
+            parent = _DEFAULT_PARENT_CHUNK_SIZE
+        child = chunking.get("child_chunk_size")
+        if child is None:
+            child = _DEFAULT_CHILD_CHUNK_SIZE
         overlap = chunking.get("chunk_overlap")
         if overlap is None:
             overlap = _DEFAULT_CHILD_CHUNK_OVERLAP
diff --git a/tests/unit/test_ingest_provenance.py b/tests/unit/test_ingest_provenance.py
index 881c483..efaea40 100644
--- a/tests/unit/test_ingest_provenance.py
+++ b/tests/unit/test_ingest_provenance.py
@@ -577,3 +577,33 @@ def test_compare_legacy_row_with_non_default_dimensions_still_drifts():
     drift = compare_ingest_config(current, at_ingest)

     assert "embedding_dimensions" in {entry["key"] for entry in drift}
+
+
+@pytest.mark.parametrize("strategy", ["sentence", "markdown"])
+def test_effective_chunking_null_sizes_resolve_to_the_defaults(strategy):
+    dm = {
+        "chunking": {
+            "strategy": strategy,
+            "parent_chunk_size": None,
+            "child_chunk_size": None,
+        }
+    }
+    result = effective_chunking(dm)
+    assert result["parent_chunk_size"] == 2048
+    assert result["child_chunk_size"] == 512
+
+
+def test_effective_chunking_null_parent_size_alone_resolves_to_the_default():
+    result = effective_chunking({"chunking": {"parent_chunk_size": None}})
+    assert result["parent_chunk_size"] == 2048
+
+
+def test_effective_chunking_null_parent_size_still_clamps_the_overlap():
+    dm = {
+        "chunking": {
+            "strategy": "sentence",
+            "parent_chunk_size": None,
+            "child_chunk_size": 30,
+        }
+    }
+    assert effective_chunking(dm)["child_chunk_overlap"] == 15
````

### Appendix C (task 2.1, docs)

````diff
diff --git a/docs/docs/configuration.md b/docs/docs/configuration.md
index eece4c6..c438a78 100644
--- a/docs/docs/configuration.md
+++ b/docs/docs/configuration.md
@@ -663,9 +663,11 @@ data_manager:
 > 10 for a size of 20), so re-ingest such a collection to apply the change. The keys
 > exist so a benchmark can sweep chunk sizes and recommend defaults from data — see
 > [Benchmarking → Hierarchical-rerank A/B](benchmarking.md#hierarchical-rerank-ab).
-> Give `parent_chunk_size` and `child_chunk_size` as plain integers; a quoted value
-> such as `"512"` stays a string after the render, and the hierarchical parser cannot
-> use it.
+> Give `parent_chunk_size` and `child_chunk_size` as plain integers of 1 or more. Any
+> other value stops the data manager and the chat service with an error that names the
+> key, the same rule as for an invalid `chunk_overlap`. A quoted value such as `"512"`
+> stays a string after the render, so it is refused too. A key with no value uses its
+> default.

 #### The `markdown` strategy

````
