"""Unit tests for hierarchical parent-child node parsing.

Exercises ``build_hierarchical_nodes``: the structure-aware splitter that
converts a LangChain ``Document`` into parent context nodes plus embedded child
leaf texts (task 2.1 of add-hierarchical-rerank-retrieval).
"""

import pytest
from langchain_core.documents import Document

from src.data_manager.vectorstore.node_parsing import (
    CHILD_EMBEDDING_DIM,
    MARKDOWN_STRATEGY,
    SENTENCE_STRATEGY,
    HierarchicalNode,
    build_hierarchical_nodes,
    embed_child_nodes,
    resolve_effective_strategy,
)


def _sentences(n: int) -> str:
    return " ".join(
        f"This is sentence number {i} with some filler words." for i in range(n)
    )


def test_sentence_strategy_produces_parents_with_children():
    """Each parent node has at least one child and the document metadata."""
    doc = Document(page_content=_sentences(120), metadata={"source": "doc.txt"})

    nodes = build_hierarchical_nodes(
        doc,
        strategy=SENTENCE_STRATEGY,
        parent_chunk_size=256,
        child_chunk_size=64,
    )

    assert nodes, "expected at least one parent node"
    assert all(isinstance(node, HierarchicalNode) for node in nodes)
    for node in nodes:
        # Spec: one or more child nodes per parent.
        assert len(node.child_texts) >= 1
        assert node.parent_text.strip()
        assert node.metadata == {"source": "doc.txt"}
    # parent_index is a stable 0-based enumeration.
    assert [node.parent_index for node in nodes] == list(range(len(nodes)))


def test_sentence_children_are_contained_in_their_parent():
    """Children are sentence-segmented pieces of their parent's context."""
    doc = Document(page_content=_sentences(120), metadata={})

    nodes = build_hierarchical_nodes(
        doc,
        strategy=SENTENCE_STRATEGY,
        parent_chunk_size=256,
        child_chunk_size=64,
    )

    # A multi-sentence corpus should split into more than one parent at this size.
    assert len(nodes) >= 2
    for node in nodes:
        for child in node.child_texts:
            # Child text is segmented on sentence boundaries: a child never ends
            # mid-sentence relative to the source (no fixed-character cut marks).
            assert child.strip() == child
            assert child in node.parent_text


def test_markdown_strategy_uses_header_sections_as_parents():
    """Markdown sections become parents, each split into sentence children."""
    md = (
        "# Title\n\n"
        "Intro paragraph one. Intro paragraph two with more text.\n\n"
        "## Section A\n\n"
        "Section A body sentence one. Section A body sentence two.\n\n"
        "## Section B\n\n"
        "Section B body content here. Another sentence in section B.\n"
    )
    doc = Document(page_content=md, metadata={"source": "guide.md"})

    nodes = build_hierarchical_nodes(doc, strategy=MARKDOWN_STRATEGY)

    # Three header-delimited sections -> three parents.
    assert len(nodes) == 3
    assert any("Section A" in node.parent_text for node in nodes)
    assert any("Section B" in node.parent_text for node in nodes)
    for node in nodes:
        assert len(node.child_texts) >= 1
        assert node.metadata["source"] == "guide.md"
        # The markdown strategy adds the header hierarchy; document metadata stays.
        assert node.metadata == {
            "source": "guide.md",
            "header_path": node.metadata["header_path"],
        }


def test_empty_document_yields_no_nodes():
    assert build_hierarchical_nodes(Document(page_content="   ", metadata={})) == []


def test_missing_page_content_yields_no_nodes():
    class _Bare:
        metadata = {"source": "x"}

    assert build_hierarchical_nodes(_Bare()) == []


def test_unsupported_strategy_raises():
    doc = Document(page_content="some text here.", metadata={})
    with pytest.raises(ValueError, match="Unsupported hierarchical chunking strategy"):
        build_hierarchical_nodes(doc, strategy="character")


class _FakeEmbedder:
    """Stand-in for archi's LangChain ``Embeddings`` model.

    Records the texts passed to ``embed_documents`` and returns vectors of a
    fixed dimension so tests can force a dimension mismatch.
    """

    def __init__(self, dim: int = CHILD_EMBEDDING_DIM):
        self.dim = dim
        self.calls: list = []

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        return [[0.0] * self.dim for _ in texts]


def test_embed_child_nodes_uses_archi_embedder():
    """Children are embedded via the provided model's ``embed_documents``."""
    embedder = _FakeEmbedder()
    children = ["child one.", "child two.", "child three."]

    embeddings = embed_child_nodes(embedder, children)

    # The configured archi embedder was used, with exactly the child texts.
    assert embedder.calls == [children]
    assert len(embeddings) == len(children)
    assert all(len(vec) == CHILD_EMBEDDING_DIM for vec in embeddings)


def test_embed_child_nodes_empty_returns_empty_without_calling_model():
    embedder = _FakeEmbedder()
    assert embed_child_nodes(embedder, []) == []
    assert embedder.calls == []


def test_embed_child_nodes_raises_on_dimension_mismatch():
    """A wrong-dimension embedding fails loudly rather than being stored."""
    embedder = _FakeEmbedder(dim=CHILD_EMBEDDING_DIM + 1)
    with pytest.raises(ValueError, match="expected 384"):
        embed_child_nodes(embedder, ["a child sentence."])


def test_embed_child_nodes_accepts_configured_non_minilm_dim():
    """A 1536-dim backend passes when ``expected_dim`` matches the config."""
    embedder = _FakeEmbedder(dim=1536)
    embeddings = embed_child_nodes(embedder, ["a child."], expected_dim=1536)
    assert [len(vec) for vec in embeddings] == [1536]


def test_embed_child_nodes_raises_when_dim_differs_from_configured():
    """A vector that differs from the configured dimension fails loudly."""
    embedder = _FakeEmbedder(dim=CHILD_EMBEDDING_DIM)
    with pytest.raises(ValueError, match="expected 1536"):
        embed_child_nodes(embedder, ["a child."], expected_dim=1536)


def test_embed_child_nodes_raises_on_count_mismatch():
    """One vector per child is required; a short result fails loudly."""

    class _ShortEmbedder:
        def embed_documents(self, texts):
            return [[0.0] * CHILD_EMBEDDING_DIM]  # one vector regardless of input

    with pytest.raises(ValueError, match="expected exactly one embedding per child"):
        embed_child_nodes(_ShortEmbedder(), ["first.", "second."])


# ---------------------------------------------------------------------------
# Markdown structural chunking (complete-markdown-chunking change)
# ---------------------------------------------------------------------------


_NESTED_MD = (
    "Preamble text before any header. It has two sentences.\n\n"
    "# Guide\n\n"
    "Guide intro sentence one. Guide intro sentence two.\n\n"
    "## Install\n\n"
    "Install body sentence one. Install body sentence two.\n\n"
    "### GPU nodes\n\n"
    "GPU body sentence one. GPU body sentence two.\n"
)


def _nodes_by_needle(nodes, needle):
    matches = [node for node in nodes if needle in node.parent_text]
    assert matches, f"no parent contains {needle!r}"
    return matches


def test_markdown_header_path_reflects_ancestor_headers():
    """Each section's metadata carries the ancestor header path."""
    doc = Document(page_content=_NESTED_MD, metadata={"source": "guide.md"})

    nodes = build_hierarchical_nodes(doc, strategy=MARKDOWN_STRATEGY)

    for node in _nodes_by_needle(nodes, "Install body"):
        assert node.metadata["header_path"] == "/Guide/"
        assert node.metadata["source"] == "guide.md"
    for node in _nodes_by_needle(nodes, "GPU body"):
        assert node.metadata["header_path"] == "/Guide/Install/"
        assert node.metadata["source"] == "guide.md"


def test_markdown_header_path_root_for_preamble_and_h1():
    """Preamble and H1 sections carry the root path; the key is never absent."""
    doc = Document(page_content=_NESTED_MD, metadata={})

    nodes = build_hierarchical_nodes(doc, strategy=MARKDOWN_STRATEGY)

    for node in _nodes_by_needle(nodes, "Preamble text"):
        assert node.metadata["header_path"] == "/"
    for node in _nodes_by_needle(nodes, "Guide intro"):
        assert node.metadata["header_path"] == "/"
    for node in nodes:
        assert "header_path" in node.metadata


def test_markdown_fenced_code_does_not_split_sections():
    """A '#' line inside a ``` fence never starts a new section."""
    md = (
        "# Scripts\n\n"
        "Intro sentence for the scripts page.\n\n"
        "```bash\n"
        "#!/bin/bash\n"
        "# comment inside the fence\n"
        "echo hi\n"
        "```\n\n"
        "Closing sentence after the fence.\n"
    )
    doc = Document(page_content=md, metadata={})

    nodes = build_hierarchical_nodes(doc, strategy=MARKDOWN_STRATEGY)

    # One header -> one section; the fence stays inside it.
    assert len(nodes) == 1
    assert "# comment inside the fence" in nodes[0].parent_text
    assert nodes[0].metadata["header_path"] == "/"


def test_markdown_tilde_fenced_code_does_not_split_sections():
    """A '#' line inside a ``~~~`` fence never starts a new section either.

    The upstream parser protects backtick fences only (PR #402 review round 3).
    A backtick line inside the tilde fence must not close it, and a real header
    after the fence must still open a section with the right ``header_path``.
    """
    md = (
        "# Scripts\n\n"
        "Intro sentence for the scripts page.\n\n"
        "~~~bash\n"
        "#!/bin/bash\n"
        "# comment inside the tilde fence\n"
        "```\n"
        "# still inside the fence\n"
        "echo hi\n"
        "~~~\n\n"
        "Closing sentence after the fence.\n\n"
        "## After\n\n"
        "Text under the real header.\n"
    )
    doc = Document(page_content=md, metadata={})

    nodes = build_hierarchical_nodes(doc, strategy=MARKDOWN_STRATEGY)

    assert [n.metadata["header_path"] for n in nodes] == ["/", "/Scripts/"]
    assert "# comment inside the tilde fence" in nodes[0].parent_text
    assert "# still inside the fence" in nodes[0].parent_text
    assert "Closing sentence after the fence." in nodes[0].parent_text
    assert nodes[1].parent_text.startswith("## After")


def test_markdown_empty_heading_is_tolerated():
    """An empty heading marker ('### ') neither crashes nor eats content."""
    md = (
        "# Top\n\n"
        "Top body sentence.\n\n"
        "### \n\n"
        "Orphan body under the empty heading.\n\n"
        "### Filesystems\n\n"
        "Filesystems body sentence.\n"
    )
    doc = Document(page_content=md, metadata={})

    nodes = build_hierarchical_nodes(doc, strategy=MARKDOWN_STRATEGY)

    joined = "\n".join(node.parent_text for node in nodes)
    assert "Orphan body under the empty heading." in joined
    assert "Filesystems body sentence." in joined


def test_markdown_oversized_section_splits_into_multiple_parents():
    """A section beyond parent_chunk_size caps into several parents."""
    body = " ".join(
        f"Section body sentence number {i} with a few filler words." for i in range(80)
    )
    md = f"# Guide\n\n## Big\n\n{body}\n"
    doc = Document(page_content=md, metadata={})

    nodes = build_hierarchical_nodes(
        doc,
        strategy=MARKDOWN_STRATEGY,
        parent_chunk_size=128,
        child_chunk_size=64,
    )

    big_parents = _nodes_by_needle(nodes, "Section body sentence number")
    assert len(big_parents) >= 2
    for node in big_parents:
        assert node.metadata["header_path"] == "/Guide/"
    # Zero-overlap pieces: every numbered sentence lands in exactly one parent.
    for i in (0, 20, 40, 79):
        needle = f"Section body sentence number {i} "
        holders = [n for n in big_parents if needle in n.parent_text]
        assert len(holders) == 1, f"sentence {i} appears in {len(holders)} parents"


def test_markdown_oversized_section_never_bisects_fences():
    """The section cap keeps fenced code blocks whole inside one parent."""
    prose = " ".join(
        f"Prose sentence number {i} with a few filler words attached."
        for i in range(40)
    )
    fence = (
        "```bash\n" + "\n".join(f"echo fence_payload_{i}" for i in range(5)) + "\n```"
    )
    md = f"# Guide\n\n## Big\n\n{prose}\n\n{fence}\n\n{prose}\n"
    doc = Document(page_content=md, metadata={})

    nodes = build_hierarchical_nodes(
        doc,
        strategy=MARKDOWN_STRATEGY,
        parent_chunk_size=128,
        child_chunk_size=64,
    )

    big_parents = [n for n in nodes if n.metadata["header_path"] == "/Guide/"]
    assert len(big_parents) >= 2, "expected the oversized section to cap"
    # Every parent holds balanced fences: never an odd number of ``` markers.
    for node in big_parents:
        fence_markers = sum(
            1
            for line in node.parent_text.split("\n")
            if line.lstrip().startswith("```")
        )
        assert fence_markers % 2 == 0, f"bisected fence in: {node.parent_text[:80]!r}"
    # The fence body is contiguous in exactly one parent.
    holders = [n for n in big_parents if "fence_payload_0" in n.parent_text]
    assert len(holders) == 1
    for i in range(5):
        assert f"fence_payload_{i}" in holders[0].parent_text


def test_markdown_fence_larger_than_parent_budget_stays_whole():
    """A fenced block bigger than parent_chunk_size becomes one whole parent."""
    fence = (
        "```bash\n"
        + "\n".join(f"echo oversized_fence_line_{i} with words" for i in range(40))
        + "\n```"
    )
    md = f"# Scripts\n\nIntro sentence.\n\n{fence}\n"
    doc = Document(page_content=md, metadata={})

    nodes = build_hierarchical_nodes(
        doc,
        strategy=MARKDOWN_STRATEGY,
        parent_chunk_size=64,
        child_chunk_size=32,
    )

    holders = [n for n in nodes if "oversized_fence_line_0" in n.parent_text]
    assert len(holders) == 1
    for i in range(40):
        assert f"oversized_fence_line_{i}" in holders[0].parent_text


def test_markdown_capped_parents_count_separators_against_budget():
    """Packed parents stay within parent_chunk_size, joins included.

    Two parts whose token counts sum exactly to the budget must not pack into
    one parent: the ``\\n\\n`` written between them costs a token too, so the
    persisted parent would exceed the cap by one (PR #402 review, round 1).
    """
    from llama_index.core.utils import get_tokenizer

    tokenizer = get_tokenizer()
    header = "## Big"
    fence_a = "```bash\n" + "\n".join(f"echo alpha_{i}" for i in range(6)) + "\n```"
    fence_b = "```bash\n" + "\n".join(f"echo beta_{i}" for i in range(6)) + "\n```"
    md = f"# Guide\n\n{header}\n\n{fence_a}\n\n{fence_b}\n"
    # Budget = header + first fence exactly, so only the join can tip it over.
    parent_chunk_size = len(tokenizer(header)) + len(tokenizer(fence_a))
    doc = Document(page_content=md, metadata={})

    nodes = build_hierarchical_nodes(
        doc,
        strategy=MARKDOWN_STRATEGY,
        parent_chunk_size=parent_chunk_size,
        child_chunk_size=32,
    )

    big_parents = [n for n in nodes if n.metadata["header_path"] == "/Guide/"]
    assert len(big_parents) >= 2, "expected the oversized section to cap"
    for node in big_parents:
        tokens = len(tokenizer(node.parent_text))
        assert tokens <= parent_chunk_size, (tokens, node.parent_text)


def test_markdown_tilde_fence_larger_than_parent_budget_stays_whole():
    """A ``~~~`` fence bigger than parent_chunk_size is as atomic as a backtick one."""
    fence = (
        "~~~bash\n"
        + "\n".join(f"echo tilde_fence_line_{i} with words" for i in range(40))
        + "\n~~~"
    )
    md = f"# Scripts\n\nIntro sentence.\n\n{fence}\n"
    doc = Document(page_content=md, metadata={})

    nodes = build_hierarchical_nodes(
        doc,
        strategy=MARKDOWN_STRATEGY,
        parent_chunk_size=64,
        child_chunk_size=32,
    )

    holders = [n for n in nodes if "tilde_fence_line_0" in n.parent_text]
    assert len(holders) == 1
    for i in range(40):
        assert f"tilde_fence_line_{i}" in holders[0].parent_text


def test_fence_segments_close_only_on_the_opening_marker():
    """A backtick line inside a tilde fence does not end it, and vice versa."""
    from src.data_manager.vectorstore.node_parsing import _fence_segments

    text = "intro\n~~~\ncode\n```\nstill code\n~~~\nafter\n```\nlast\n~~~\n```"

    assert _fence_segments(text) == [
        (False, "intro"),
        (True, "~~~\ncode\n```\nstill code\n~~~"),
        (False, "after"),
        (True, "```\nlast\n~~~\n```"),
    ]


def test_fence_segments_honor_the_opening_run_length():
    """A longer fence run is closed only by a run of the same char, at least as long.

    CommonMark lets a ```` fence hold a literal ``` line (PR #402 review round 4).
    """
    from src.data_manager.vectorstore.node_parsing import _fence_segments

    text = "intro\n````md\n```\n# literal\n```\n````\nafter\n~~~~\n~~~\n~~~~~\nlast"

    assert _fence_segments(text) == [
        (False, "intro"),
        (True, "````md\n```\n# literal\n```\n````"),
        (False, "after"),
        (True, "~~~~\n~~~\n~~~~~"),
        (False, "last"),
    ]


def test_markdown_longer_fence_run_protects_inner_triple_and_headers():
    """A ```` block with an inner ``` line and a '#' line stays one section and one parent."""
    inner = "\n".join(f"echo long_fence_line_{i} with words" for i in range(40))
    md = (
        "# Docs\n\n"
        "How to show a fence inside a fence.\n\n"
        "````markdown\n"
        "```bash\n"
        "# not a header\n"
        f"{inner}\n"
        "```\n"
        "````\n\n"
        "## After\n\n"
        "Text under the real header.\n"
    )
    doc = Document(page_content=md, metadata={})

    nodes = build_hierarchical_nodes(
        doc,
        strategy=MARKDOWN_STRATEGY,
        parent_chunk_size=64,
        child_chunk_size=32,
    )

    # The oversized first section caps into a prose parent and the whole-fence
    # parent (both under the H1); "# not a header" opens no section of its own.
    assert [n.metadata["header_path"] for n in nodes] == ["/", "/", "/Docs/"]
    assert not any(n.parent_text.startswith("# not a header") for n in nodes)
    holders = [n for n in nodes if "long_fence_line_0" in n.parent_text]
    assert len(holders) == 1
    assert "# not a header" in holders[0].parent_text
    for i in range(40):
        assert f"long_fence_line_{i}" in holders[0].parent_text


def test_markdown_capped_parents_are_verbatim_slices_of_the_source():
    """Repacked parents reproduce the source text; no separators are invented.

    A long unbroken URL makes SentenceSplitter cut mid-token into pieces far
    below the cap in tokens, so several pieces pack into one parent. They must
    come back together with nothing inserted between them (PR #402 round 2).
    """
    url = "https://example.org/" + "/".join(f"segment{i:03d}" for i in range(60))
    prose = " ".join(f"Prose sentence number {i} with filler words." for i in range(6))
    md = f"# Links\n\n## Big\n\n{prose} See {url} for details.\n\n{prose}\n"
    doc = Document(page_content=md, metadata={})

    nodes = build_hierarchical_nodes(
        doc,
        strategy=MARKDOWN_STRATEGY,
        parent_chunk_size=64,
        child_chunk_size=32,
    )

    big_parents = [n for n in nodes if n.metadata["header_path"] == "/Links/"]
    assert len(big_parents) >= 2, "expected the oversized section to cap"
    for node in big_parents:
        assert node.parent_text in md, node.parent_text[:120]
    # The URL has no whitespace, so the parents that hold it concatenate back
    # to the exact URL — nothing was injected at the cuts.
    assert url in "".join(n.parent_text for n in big_parents)


def test_cap_section_falls_back_to_join_when_a_piece_is_not_in_the_source():
    """A splitter that rewrites whitespace still packs; the join path is the fallback."""
    from src.data_manager.vectorstore.node_parsing import _cap_section

    class _RewritingSplitter:
        def split_text(self, text):
            # Two pieces so the section counts as oversized; the double space
            # in the first piece is not in the source, so it cannot be located.
            return ["alpha  beta", "gamma delta"]

    capped = _cap_section("alpha beta gamma delta", _RewritingSplitter(), 1000)

    assert capped == ["alpha  beta\n\ngamma delta"]


def test_clamped_overlap_boundary_values():
    """The clamp preserves legal overlaps exactly (upstream raises only on >)."""
    from src.data_manager.vectorstore.node_parsing import _clamped_overlap

    assert _clamped_overlap(0) == 0
    assert _clamped_overlap(1) == 1
    assert _clamped_overlap(20) == 20  # unchanged topology at the boundary
    assert _clamped_overlap(21) == 20
    assert _clamped_overlap(200) == 20


def test_small_child_chunk_size_does_not_raise():
    """child_chunk_size below the splitter's 200-token default overlap works."""
    md = "# T\n\nBody sentence one. Body sentence two. Body sentence three.\n"
    for strategy in (MARKDOWN_STRATEGY, SENTENCE_STRATEGY):
        nodes = build_hierarchical_nodes(
            Document(page_content=md, metadata={}),
            strategy=strategy,
            parent_chunk_size=256,
            child_chunk_size=64,
        )
        assert nodes, f"{strategy}: expected nodes for a small child size"
    # A tiny child size still works on the markdown path (overlap clamps below it).
    tiny = build_hierarchical_nodes(
        Document(page_content=md, metadata={}),
        strategy=MARKDOWN_STRATEGY,
        parent_chunk_size=256,
        child_chunk_size=16,
    )
    assert tiny


def test_sentence_strategy_metadata_unchanged_by_markdown_completion():
    """The sentence path adds no extra metadata keys (refactor guard)."""
    doc = Document(page_content=_sentences(40), metadata={"source": "doc.txt"})

    nodes = build_hierarchical_nodes(
        doc,
        strategy=SENTENCE_STRATEGY,
        parent_chunk_size=256,
        child_chunk_size=64,
    )

    for node in nodes:
        assert node.metadata == {"source": "doc.txt"}


@pytest.mark.parametrize("suffix", ["md", ".md", "MD", "markdown", ".MARKDOWN"])
def test_resolve_effective_strategy_accepts_markdown_suffixes(suffix):
    """The recorded suffix is authoritative, dotted or not, any case."""
    assert (
        resolve_effective_strategy(MARKDOWN_STRATEGY, filename="x.bin", suffix=suffix)
        == MARKDOWN_STRATEGY
    )


@pytest.mark.parametrize("suffix", ["py", ".py", "txt", "pdf", "html"])
def test_resolve_effective_strategy_falls_back_to_sentence(suffix):
    """Non-Markdown files take the sentence strategy under strategy=markdown."""
    assert (
        resolve_effective_strategy(MARKDOWN_STRATEGY, filename="x.md", suffix=suffix)
        == SENTENCE_STRATEGY
    )


def test_resolve_effective_strategy_filename_fallback():
    """The filename extension decides when no suffix is recorded."""
    assert (
        resolve_effective_strategy(MARKDOWN_STRATEGY, filename="guide.md", suffix=None)
        == MARKDOWN_STRATEGY
    )
    assert (
        resolve_effective_strategy(MARKDOWN_STRATEGY, filename="script.py", suffix=None)
        == SENTENCE_STRATEGY
    )
    assert (
        resolve_effective_strategy(MARKDOWN_STRATEGY, filename=None, suffix=None)
        == SENTENCE_STRATEGY
    )


def test_resolve_effective_strategy_passes_other_strategies_through():
    """Only strategy=markdown dispatches; every other value is untouched."""
    assert (
        resolve_effective_strategy(SENTENCE_STRATEGY, filename="guide.md", suffix="md")
        == SENTENCE_STRATEGY
    )
    assert (
        resolve_effective_strategy("character", filename="guide.md", suffix="md")
        == "character"
    )
