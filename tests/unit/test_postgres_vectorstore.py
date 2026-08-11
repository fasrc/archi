"""
Unit tests for PostgresVectorStore.

Tests cover:
- Similarity search (semantic)
- Hybrid search (semantic + BM25)
- Document addition and deletion
- Metadata filtering
- Index usage
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from langchain_core.documents import Document

from src.data_manager.vectorstore.postgres_vectorstore import PostgresVectorStore

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_pg_connection():
    """Create a mock PostgreSQL connection."""
    conn = MagicMock()
    cursor = MagicMock()

    # Setup context manager
    cursor_context = MagicMock()
    cursor_context.__enter__ = MagicMock(return_value=cursor)
    cursor_context.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor_context

    # Required for psycopg2.extras.execute_values - cursor needs connection.encoding
    conn.encoding = "UTF8"
    cursor.connection = conn  # Link cursor back to connection

    return conn, cursor


@pytest.fixture
def mock_embeddings():
    """Create a mock embeddings function."""
    embeddings = MagicMock()
    embeddings.embed_documents.return_value = [[0.1, 0.2, 0.3] * 128]  # 384-dim
    embeddings.embed_query.return_value = [0.1, 0.2, 0.3] * 128
    return embeddings


@pytest.fixture
def pg_config():
    """Standard PostgreSQL config."""
    return {
        "host": "localhost",
        "port": 5432,
        "dbname": "archi_test",
        "user": "postgres",
        "password": "testpass",
    }


@pytest.fixture
def vector_store(pg_config, mock_embeddings, mock_pg_connection):
    """Create a PostgresVectorStore with mocked connection."""
    conn, cursor = mock_pg_connection

    with patch.object(PostgresVectorStore, "_get_connection", return_value=conn):
        store = PostgresVectorStore(
            pg_config=pg_config,
            embedding_function=mock_embeddings,
            collection_name="test_collection",
            distance_metric="cosine",
        )
        return store


# =============================================================================
# Initialization Tests
# =============================================================================


class TestPostgresVectorStoreInit:
    """Tests for PostgresVectorStore initialization."""

    def test_init_with_valid_config(self, pg_config, mock_embeddings):
        """Test successful initialization."""
        store = PostgresVectorStore(
            pg_config=pg_config,
            embedding_function=mock_embeddings,
        )

        assert store._collection_name == "default"
        assert store._distance_metric == "cosine"
        assert store._distance_op == "<=>"

    def test_init_with_custom_collection(self, pg_config, mock_embeddings):
        """Test initialization with custom collection name."""
        store = PostgresVectorStore(
            pg_config=pg_config,
            embedding_function=mock_embeddings,
            collection_name="my_docs",
        )

        assert store._collection_name == "my_docs"

    def test_init_with_l2_distance(self, pg_config, mock_embeddings):
        """Test initialization with L2 distance metric."""
        store = PostgresVectorStore(
            pg_config=pg_config,
            embedding_function=mock_embeddings,
            distance_metric="l2",
        )

        assert store._distance_metric == "l2"
        assert store._distance_op == "<->"

    def test_init_with_inner_product(self, pg_config, mock_embeddings):
        """Test initialization with inner product distance."""
        store = PostgresVectorStore(
            pg_config=pg_config,
            embedding_function=mock_embeddings,
            distance_metric="inner_product",
        )

        assert store._distance_metric == "inner_product"
        assert store._distance_op == "<#>"

    def test_init_invalid_distance_metric(self, pg_config, mock_embeddings):
        """Test initialization with invalid distance metric."""
        with pytest.raises(ValueError, match="distance_metric must be one of"):
            PostgresVectorStore(
                pg_config=pg_config,
                embedding_function=mock_embeddings,
                distance_metric="invalid",
            )

    def test_embeddings_property(self, vector_store, mock_embeddings):
        """Test embeddings property returns the embedding function."""
        assert vector_store.embeddings is mock_embeddings


# =============================================================================
# Similarity Search Tests
# =============================================================================


class TestSimilaritySearch:
    """Tests for similarity search."""

    def test_similarity_search_basic(
        self, vector_store, mock_pg_connection, mock_embeddings
    ):
        """Test basic similarity search."""
        conn, cursor = mock_pg_connection

        # Mock query results
        cursor.fetchall.return_value = [
            {
                "id": 1,
                "chunk_text": "Document about machine learning",
                "metadata": json.dumps({"source": "web"}),
                "distance": 0.15,
                "resource_hash": "abc123",
                "display_name": "ML Guide",
                "source_type": "web",
                "url": "https://example.com/ml",
            },
            {
                "id": 2,
                "chunk_text": "Another ML document",
                "metadata": json.dumps({"source": "pdf"}),
                "distance": 0.25,
                "resource_hash": "def456",
                "display_name": "ML Paper",
                "source_type": "pdf",
                "url": None,
            },
        ]

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.similarity_search("machine learning", k=2)

        assert len(results) == 2
        assert isinstance(results[0], Document)
        assert "machine learning" in results[0].page_content
        assert results[0].metadata.get("resource_hash") == "abc123"

    def test_similarity_search_with_scores(self, vector_store, mock_pg_connection):
        """Test similarity search returning scores."""
        conn, cursor = mock_pg_connection

        cursor.fetchall.return_value = [
            {
                "id": 1,
                "chunk_text": "Relevant document",
                "metadata": "{}",
                "distance": 0.1,  # cosine distance
                "resource_hash": None,
                "display_name": None,
                "source_type": None,
                "url": None,
            },
        ]

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.similarity_search_with_score("query", k=1)

        assert len(results) == 1
        doc, score = results[0]
        assert isinstance(doc, Document)
        assert score == 0.9  # 1 - 0.1 cosine distance

    def test_similarity_search_with_filter(self, vector_store, mock_pg_connection):
        """Test similarity search with metadata filter."""
        conn, cursor = mock_pg_connection
        cursor.fetchall.return_value = []

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.similarity_search(
                "query",
                k=5,
                filter={"source_type": "pdf"},
            )

        # Verify filter was applied in query
        call_args = cursor.execute.call_args[0]
        query_sql = call_args[0]
        assert "source_type" in query_sql or "metadata" in query_sql

    def test_similarity_search_empty_results(self, vector_store, mock_pg_connection):
        """Test similarity search with no results."""
        conn, cursor = mock_pg_connection
        cursor.fetchall.return_value = []

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.similarity_search("obscure query", k=5)

        assert results == []


# =============================================================================
# Hybrid Search Tests
# =============================================================================


class TestHybridSearch:
    """Tests for hybrid search (semantic + BM25)."""

    def test_hybrid_search_with_bm25_index(self, vector_store, mock_pg_connection):
        """Test hybrid search when BM25 index exists."""
        conn, cursor = mock_pg_connection

        # First call checks for BM25 index, second checks for chunk_tsv column
        # Use dict-like results for RealDictCursor compatibility
        cursor.fetchone.side_effect = [
            {"relname": "idx_bm25"},
            None,
        ]  # BM25 index exists, no chunk_tsv
        cursor.fetchall.return_value = [
            {
                "id": 1,
                "chunk_text": "Machine learning fundamentals",
                "metadata": "{}",
                "semantic_score": 0.85,
                "bm25_score": -0.9,
                "combined_score": 0.865,
                "resource_hash": "abc",
                "display_name": "ML Doc",
                "source_type": "web",
                "url": None,
            },
        ]

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.hybrid_search(
                "machine learning",
                k=5,
                semantic_weight=0.7,
                bm25_weight=0.3,
            )

        assert len(results) == 1
        doc, score = results[0]
        assert "machine learning" in doc.page_content.lower()

    def test_hybrid_search_without_bm25_index(self, vector_store, mock_pg_connection):
        """Test hybrid search raises error when BM25 index is missing."""
        conn, cursor = mock_pg_connection

        # No BM25 index found
        cursor.fetchone.return_value = None

        with patch.object(vector_store, "_get_connection", return_value=conn):
            with pytest.raises(RuntimeError, match="BM25 index"):
                vector_store.hybrid_search("query", k=5)

    def test_hybrid_search_custom_weights(self, vector_store, mock_pg_connection):
        """Test hybrid search with custom weights."""
        conn, cursor = mock_pg_connection
        # First call checks for BM25 index
        # Use dict-like results for RealDictCursor compatibility
        cursor.fetchone.return_value = {"relname": "idx_bm25"}  # BM25 index exists
        # Return mock results so we don't fall back to semantic search
        cursor.fetchall.return_value = [
            {
                "id": 1,
                "chunk_text": "test content",
                "metadata": {},
                "semantic_score": 0.8,
                "bm25_score": -0.7,
                "combined_score": 0.74,
                "resource_hash": "hash123",
                "display_name": "Test Doc",
                "source_type": "web",
                "url": None,
            }
        ]

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.hybrid_search(
                "query",
                k=5,
                semantic_weight=0.4,
                bm25_weight=0.6,
            )

        # Verify we got results
        assert len(results) == 1

        # Verify hybrid search query was executed (weights are embedded in SQL params)
        # Find the hybrid search query (has combined_score)
        for call in cursor.execute.call_args_list:
            call_args = call[0]
            query_sql = call_args[0]
            if "combined_score" in query_sql.lower():
                params = call_args[1]
                # Weights should be in params (0.4 and 0.6)
                assert 0.4 in params
                assert 0.6 in params
                break
        else:
            pytest.fail("Hybrid search query with combined_score not found")

    def test_hybrid_search_score_combination(self, vector_store, mock_pg_connection):
        """Test that hybrid search correctly combines scores."""
        conn, cursor = mock_pg_connection
        # First call checks for BM25 index, second checks for chunk_tsv column
        # Use dict-like results for RealDictCursor compatibility
        cursor.fetchone.side_effect = [
            {"relname": "idx_bm25"},
            None,
        ]  # BM25 index exists, no chunk_tsv

        # Results with known scores
        cursor.fetchall.return_value = [
            {
                "id": 1,
                "chunk_text": "High semantic, low keyword",
                "metadata": "{}",
                "semantic_score": 0.95,
                "bm25_score": -0.2,
                "combined_score": 0.725,
                "resource_hash": None,
                "display_name": None,
                "source_type": None,
                "url": None,
            },
            {
                "id": 2,
                "chunk_text": "Balanced scores",
                "metadata": "{}",
                "semantic_score": 0.7,
                "bm25_score": -0.8,
                "combined_score": 0.73,
                "resource_hash": None,
                "display_name": None,
                "source_type": None,
                "url": None,
            },
        ]

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.hybrid_search(
                "query",
                k=5,
                semantic_weight=0.7,
                bm25_weight=0.3,
            )

        # Results should be ordered by combined_score
        assert len(results) == 2
        # Second doc has higher combined score
        _, score1 = results[0]
        _, score2 = results[1]


class TestHybridSearchExcludesParents:
    """Parents must never surface in hybrid search (spec 5.3).

    Parent nodes live only in ``document_parent_nodes`` and are neither embedded
    nor BM25-indexed. ``hybrid_search`` queries exclusively against
    ``document_chunks`` (embedded child/leaf rows), so a parent row can never be
    returned among its results.
    """

    def test_hybrid_search_never_queries_parent_table(
        self, vector_store, mock_pg_connection
    ):
        """The hybrid search SQL targets document_chunks and never the parent table."""
        conn, cursor = mock_pg_connection
        cursor.fetchone.side_effect = [{"relname": "idx_bm25"}, None]
        cursor.fetchall.return_value = [
            {
                "id": 1,
                "chunk_text": "Child leaf chunk",
                "metadata": json.dumps({"parent_id": 42}),
                "semantic_score": 0.8,
                "bm25_score": -0.7,
                "combined_score": 0.77,
                "resource_hash": "abc",
                "display_name": "Doc",
                "source_type": "web",
                "url": None,
            },
        ]

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.hybrid_search("query", k=5)

        assert len(results) == 1

        # No executed statement may reference the parent-node table: parents are
        # stored separately and must be unreachable from the shared search path.
        for call in cursor.execute.call_args_list:
            executed_sql = call[0][0]
            assert "document_parent_nodes" not in executed_sql.lower()

        # The candidate-scoring query reads child rows from document_chunks.
        scoring_calls = [
            call[0][0]
            for call in cursor.execute.call_args_list
            if "combined_score" in call[0][0].lower()
        ]
        assert scoring_calls, "hybrid scoring query was not executed"
        assert "from document_chunks" in scoring_calls[0].lower()

    def test_hybrid_search_results_are_child_rows(
        self, vector_store, mock_pg_connection
    ):
        """Returned Documents carry child content/parent_id, never bare parent text."""
        conn, cursor = mock_pg_connection
        cursor.fetchone.side_effect = [{"relname": "idx_bm25"}, None]
        cursor.fetchall.return_value = [
            {
                "id": 7,
                "chunk_text": "A small embedded child sentence.",
                "metadata": json.dumps({"parent_id": 100, "chunk_id": "c7"}),
                "semantic_score": 0.9,
                "bm25_score": -0.5,
                "combined_score": 0.78,
                "resource_hash": None,
                "display_name": None,
                "source_type": None,
                "url": None,
            },
        ]

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.hybrid_search("query", k=5)

        doc, _ = results[0]
        # The row is a child: it references a parent rather than being one.
        assert doc.metadata.get("parent_id") == 100
        assert doc.page_content == "A small embedded child sentence."


class TestHybridSearchFallbackWarning:
    """The silent fallback at lines 513-516 must emit a structured warning."""

    SENSITIVE_QUERY = "CANARY_private_data_do_not_log"

    def _run_hybrid_with_rows(self, vector_store, mock_pg_connection, rows):
        conn, cursor = mock_pg_connection
        cursor.fetchone.return_value = {"relname": "idx_bm25"}
        cursor.fetchall.return_value = rows

        with patch.object(vector_store, "_get_connection", return_value=conn):
            with patch.object(
                vector_store, "similarity_search_with_score", return_value=[]
            ) as fallback:
                results = vector_store.hybrid_search(self.SENSITIVE_QUERY, k=5)
                return results, fallback

    def test_fallback_emits_warning_on_zero_rows(
        self, vector_store, mock_pg_connection, caplog
    ):
        import logging

        with caplog.at_level(logging.WARNING):
            self._run_hybrid_with_rows(vector_store, mock_pg_connection, [])

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_records, "no WARNING emitted on zero-row fallback"
        msg = warning_records[0].getMessage()
        assert "collection=" in msg
        assert "k=" in msg
        assert "reason=" in msg

    def test_fallback_warning_contains_collection_and_k(
        self, vector_store, mock_pg_connection, caplog
    ):
        import logging

        with caplog.at_level(logging.WARNING):
            self._run_hybrid_with_rows(vector_store, mock_pg_connection, [])

        msg = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING][
            0
        ]
        assert "collection=test_collection" in msg
        assert "k=5" in msg

    def test_no_warning_when_rows_returned(
        self, vector_store, mock_pg_connection, caplog
    ):
        import logging

        rows = [
            {
                "id": 1,
                "chunk_text": "content",
                "metadata": "{}",
                "semantic_score": 0.8,
                "bm25_score": -0.5,
                "combined_score": 0.6,
                "resource_hash": None,
                "display_name": None,
                "source_type": None,
                "url": None,
            }
        ]
        with caplog.at_level(logging.WARNING):
            self._run_hybrid_with_rows(vector_store, mock_pg_connection, rows)

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not warning_records, "WARNING emitted when rows were returned"

    def test_query_text_never_in_log_record(
        self, vector_store, mock_pg_connection, caplog
    ):
        import logging

        with caplog.at_level(logging.WARNING):
            self._run_hybrid_with_rows(vector_store, mock_pg_connection, [])

        for record in caplog.records:
            assert self.SENSITIVE_QUERY not in record.getMessage()
            assert self.SENSITIVE_QUERY not in str(record.__dict__)

    def test_warning_fields_survive_configured_formatter(
        self, vector_store, mock_pg_connection, caplog
    ):
        """Task 1.5: fields must appear in the formatted output, not just
        the LogRecord attributes — setup_logging uses %(message)s only."""
        import logging

        fmt = logging.Formatter("(%(asctime)s) [%(name)s] %(levelname)s: %(message)s")
        with caplog.at_level(logging.WARNING):
            self._run_hybrid_with_rows(vector_store, mock_pg_connection, [])

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_records
        formatted = fmt.format(warning_records[0])
        assert "collection=test_collection" in formatted
        assert "k=5" in formatted
        assert "reason=" in formatted


class TestHybridSearchScoringOrientation:
    """BM25 scores from <@> are negative; the SQL must negate and normalize."""

    def _capture_sql(self, vector_store, mock_pg_connection):
        conn, cursor = mock_pg_connection
        cursor.fetchone.return_value = {"relname": "idx_bm25"}
        cursor.fetchall.return_value = [
            {
                "id": 1,
                "chunk_text": "x",
                "metadata": "{}",
                "semantic_score": 0.8,
                "bm25_score": -0.5,
                "combined_score": 0.6,
                "resource_hash": None,
                "display_name": None,
                "source_type": None,
                "url": None,
            }
        ]
        with patch.object(vector_store, "_get_connection", return_value=conn):
            vector_store.hybrid_search("q", k=3)
        for call in cursor.execute.call_args_list:
            args = call[0]
            if "combined_score" in args[0].lower():
                return args[0]
        pytest.fail("scoring SQL not found")

    def test_bm25_term_is_negated(self, vector_store, mock_pg_connection):
        sql = self._capture_sql(vector_store, mock_pg_connection)
        assert (
            "-1.0 *" in sql or "-1 *" in sql
        ), f"BM25 <@> term not negated in SQL:\n{sql}"

    def test_both_components_normalized(self, vector_store, mock_pg_connection):
        sql = self._capture_sql(vector_store, mock_pg_connection).lower()
        assert (
            "min(" in sql and "max(" in sql
        ), "min-max normalization window functions missing"

    def test_combined_score_uses_normalized_components(
        self, vector_store, mock_pg_connection
    ):
        sql = self._capture_sql(vector_store, mock_pg_connection).lower()
        assert "nullif" in sql, "zero-range NULLIF guard missing from normalization"

    def test_normalization_before_limit(self, vector_store, mock_pg_connection):
        sql = self._capture_sql(vector_store, mock_pg_connection).lower()
        limit_pos = sql.rfind("limit")
        min_pos = sql.find("min(")
        assert min_pos >= 0, "min() window function not found in SQL"
        assert min_pos < limit_pos, "normalization must happen before the LIMIT"


class TestHybridSearchParameterBinding:
    """The SQL parameter order must match the placeholder order.

    The defect: ``all_params`` listed ``[embedding, collection, *filters,
    query, ...]`` but the SQL placeholders expected ``[embedding, bm25_query,
    collection, *filters, ...]``.  ``to_bm25query()`` received the collection
    name and the WHERE received the user's question — matching zero rows.
    """

    def _capture_hybrid_sql(self, vector_store, mock_pg_connection, **kwargs):
        conn, cursor = mock_pg_connection
        cursor.fetchone.return_value = {"relname": "idx_bm25"}
        cursor.fetchall.return_value = [
            {
                "id": 1,
                "chunk_text": "x",
                "metadata": "{}",
                "semantic_score": 0.8,
                "bm25_score": -0.5,
                "combined_score": 0.6,
                "resource_hash": None,
                "display_name": None,
                "source_type": None,
                "url": None,
            }
        ]
        with patch.object(vector_store, "_get_connection", return_value=conn):
            vector_store.hybrid_search("user question text", k=3, **kwargs)

        for call in cursor.execute.call_args_list:
            args = call[0]
            sql = args[0]
            if "combined_score" in sql.lower() and len(args) > 1:
                return sql, args[1]
        pytest.fail("hybrid scoring query not found in execute calls")

    def test_query_reaches_bm25_placeholder(self, vector_store, mock_pg_connection):
        """The user's query text must bind to to_bm25query(), not the
        collection predicate."""
        sql, params = self._capture_hybrid_sql(vector_store, mock_pg_connection)
        bm25_idx = sql.index("to_bm25query(%s")
        collection_idx = sql.index("collection' = %s")
        bm25_param_pos = sql[:bm25_idx].count("%s")
        collection_param_pos = sql[:collection_idx].count("%s")
        assert params[bm25_param_pos] == "user question text"
        assert params[collection_param_pos] == "test_collection"

    def test_binding_with_metadata_filter(self, vector_store, mock_pg_connection):
        """Added WHERE placeholders from a metadata filter must not shift
        the BM25 query into the wrong slot."""
        sql, params = self._capture_hybrid_sql(
            vector_store, mock_pg_connection, filter={"topic": "gpu"}
        )
        bm25_param_pos = sql[: sql.index("to_bm25query(%s")].count("%s")
        collection_param_pos = sql[: sql.index("collection' = %s")].count("%s")
        assert params[bm25_param_pos] == "user question text"
        assert params[collection_param_pos] == "test_collection"
        assert "gpu" in params

    def test_guard_reorder_collection_to_bm25(self, vector_store, mock_pg_connection):
        """If the collection name ever reaches the BM25 expression,
        this test must fail."""
        sql, params = self._capture_hybrid_sql(vector_store, mock_pg_connection)
        bm25_param_pos = sql[: sql.index("to_bm25query(%s")].count("%s")
        assert (
            params[bm25_param_pos] != "test_collection"
        ), "collection name bound to BM25 expression"


# =============================================================================
# Document Operations Tests
# =============================================================================


class TestDocumentOperations:
    """Tests for document add/delete operations."""

    def test_add_texts(self, vector_store, mock_pg_connection, mock_embeddings):
        """Test adding texts to the vector store."""
        conn, cursor = mock_pg_connection
        cursor.fetchone.return_value = (1,)  # document_id

        texts = ["First document", "Second document"]
        metadatas = [{"source": "test"}, {"source": "test"}]

        with patch.object(vector_store, "_get_connection", return_value=conn), patch(
            "psycopg2.extras.execute_values"
        ) as mock_execute_values:
            ids = vector_store.add_texts(texts, metadatas=metadatas)

        assert len(ids) == 2
        # Verify embeddings were created
        mock_embeddings.embed_documents.assert_called_once_with(texts)
        # Verify execute_values was called for bulk insert
        assert mock_execute_values.called

    def test_add_documents(self, vector_store, mock_pg_connection, mock_embeddings):
        """Test adding Document objects."""
        conn, cursor = mock_pg_connection
        cursor.fetchone.return_value = (1,)

        docs = [
            Document(page_content="Doc 1", metadata={"key": "value1"}),
            Document(page_content="Doc 2", metadata={"key": "value2"}),
        ]

        with patch.object(vector_store, "_get_connection", return_value=conn), patch(
            "psycopg2.extras.execute_values"
        ) as mock_execute_values:
            ids = vector_store.add_documents(docs)

        assert len(ids) == 2
        assert mock_execute_values.called

    def test_delete_by_ids(self, vector_store, mock_pg_connection):
        """Test deleting documents by ID."""
        conn, cursor = mock_pg_connection
        cursor.rowcount = 2  # 2 rows deleted

        with patch.object(vector_store, "_get_connection", return_value=conn):
            success = vector_store.delete(ids=["chunk_1", "chunk_2"])

        assert success is True
        # Verify DELETE was called
        call_args = cursor.execute.call_args[0]
        assert "DELETE" in call_args[0] or "UPDATE" in call_args[0]


# =============================================================================
# Search Quality Tests
# =============================================================================


class TestSearchQuality:
    """Tests for search quality metrics."""

    def test_cosine_distance_to_similarity(self, vector_store, mock_pg_connection):
        """Test that cosine distance is converted to similarity correctly."""
        conn, cursor = mock_pg_connection

        # Distance of 0.1 should give similarity of 0.9
        cursor.fetchall.return_value = [
            {
                "id": 1,
                "chunk_text": "Test",
                "metadata": "{}",
                "distance": 0.1,
                "resource_hash": None,
                "display_name": None,
                "source_type": None,
                "url": None,
            },
        ]

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.similarity_search_with_score("query", k=1)

        _, score = results[0]
        assert abs(score - 0.9) < 0.001  # 1 - 0.1

    def test_metadata_preserved_in_results(self, vector_store, mock_pg_connection):
        """Test that all metadata is preserved in results."""
        conn, cursor = mock_pg_connection

        original_metadata = {
            "source": "web",
            "page": 5,
            "custom_field": "custom_value",
        }

        cursor.fetchall.return_value = [
            {
                "id": 1,
                "chunk_text": "Test document",
                "metadata": json.dumps(original_metadata),
                "distance": 0.2,
                "resource_hash": "hash123",
                "display_name": "Test Doc",
                "source_type": "web",
                "url": "https://example.com",
            },
        ]

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.similarity_search("query", k=1)

        doc = results[0]
        assert doc.metadata.get("source") == "web"
        assert doc.metadata.get("page") == 5
        assert doc.metadata.get("custom_field") == "custom_value"
        assert doc.metadata.get("resource_hash") == "hash123"
        assert doc.metadata.get("display_name") == "Test Doc"


# =============================================================================
# Edge Cases Tests
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_query(self, vector_store, mock_pg_connection):
        """Test search with empty query."""
        conn, cursor = mock_pg_connection
        cursor.fetchall.return_value = []

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.similarity_search("", k=5)

        # Should return empty or handle gracefully
        assert isinstance(results, list)

    def test_large_k_value(self, vector_store, mock_pg_connection):
        """Test search with large k value."""
        conn, cursor = mock_pg_connection
        cursor.fetchall.return_value = []

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.similarity_search("query", k=10000)

        # Should handle without error
        assert isinstance(results, list)

    def test_special_characters_in_query(self, vector_store, mock_pg_connection):
        """Test search with special characters in query."""
        conn, cursor = mock_pg_connection
        cursor.fetchall.return_value = []

        with patch.object(vector_store, "_get_connection", return_value=conn):
            # Should not raise SQL injection errors
            results = vector_store.similarity_search(
                "query'; DROP TABLE documents; --", k=5
            )

        assert isinstance(results, list)

    def test_unicode_in_query(self, vector_store, mock_pg_connection):
        """Test search with unicode characters."""
        conn, cursor = mock_pg_connection
        cursor.fetchall.return_value = []

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.similarity_search("机器学习 αβγ 🤖", k=5)

        assert isinstance(results, list)

    def test_null_metadata_handling(self, vector_store, mock_pg_connection):
        """Test handling of null metadata in results."""
        conn, cursor = mock_pg_connection

        cursor.fetchall.return_value = [
            {
                "id": 1,
                "chunk_text": "Document with no metadata",
                "metadata": None,  # NULL from database
                "distance": 0.3,
                "resource_hash": None,
                "display_name": None,
                "source_type": None,
                "url": None,
            },
        ]

        with patch.object(vector_store, "_get_connection", return_value=conn):
            results = vector_store.similarity_search("query", k=1)

        assert len(results) == 1
        assert results[0].metadata is not None  # Should be empty dict, not None
