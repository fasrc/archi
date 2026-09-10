"""Boolean flags in base-config.yaml must honour an explicitly configured
``false`` value rather than replacing it with the template default, and must
render an explicit ``null`` as the row's default rather than the string 'None'.

Covers all 21 bug-class sites and all 7 null-class sites from the 28-conversion
table in tasks.md.  Three inputs per key: configured false → False, absent →
default, explicit None → default.
"""

import pytest
import yaml
from jinja2 import (
    ChainableUndefined,
    Environment,
    PackageLoader,
    nodes,
    select_autoescape,
)


def _render(**kwargs):
    env = Environment(
        loader=PackageLoader("src.cli"),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    template = env.get_template("base-config.yaml")
    rendered = template.render(verbosity=0, **kwargs)
    return yaml.safe_load(rendered)


def _expand(dotted_path, value):
    """Build the nested template-variable dict from a dotted input path.

    ``"a.b.c"`` with value ``v`` → ``{"a": {"b": {"c": v}}}``.
    The first component is the template variable name; the rest nest inside it.
    """
    parts = dotted_path.split(".")
    result = value
    for part in reversed(parts[1:]):
        result = {part: result}
    return {parts[0]: result}


def _get(cfg, dotted_path):
    """Read a dotted path from the rendered config dict."""
    node = cfg
    for part in dotted_path.split("."):
        node = node[part]
    return node


# ---------------------------------------------------------------------------
# All 28 rows — (input_path, rendered_path, default, absent_kwargs)
# Listed in template line order.  absent_kwargs is merged into _render() for
# the absent-input case; it is {} for most rows, but non-empty for :43 where
# the enclosing {%- if %} block only renders when anchors is truthy.
# ---------------------------------------------------------------------------

_BUG_ROWS = [
    (
        "services.data_manager.enabled",
        "services.data_manager.enabled",
        True,
        {},
    ),
    (
        "data_manager.embedding_class_map.HuggingFaceEmbeddings.kwargs.encode_kwargs.normalize_embeddings",
        "data_manager.embedding_class_map.HuggingFaceEmbeddings.kwargs.encode_kwargs.normalize_embeddings",
        True,
        {},
    ),
    (
        "data_manager.reset_collection",
        "data_manager.reset_collection",
        True,
        {},
    ),
    (
        "data_manager.sources.local_files.enabled",
        "data_manager.sources.local_files.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.local_files.visible",
        "data_manager.sources.local_files.visible",
        True,
        {},
    ),
    (
        "data_manager.sources.links.enabled",
        "data_manager.sources.links.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.links.visible",
        "data_manager.sources.links.visible",
        True,
        {},
    ),
    (
        "data_manager.sources.links.html_scraper.reset_data",
        "data_manager.sources.links.html_scraper.reset_data",
        True,
        {},
    ),
    (
        "data_manager.sources.links.selenium_scraper.selenium_class_map.CERNSSOScraper.kwargs.headless",
        "data_manager.sources.links.selenium_scraper.selenium_class_map.CERNSSOScraper.kwargs.headless",
        True,
        {},
    ),
    (
        "data_manager.sources.git.enabled",
        "data_manager.sources.git.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.git.visible",
        "data_manager.sources.git.visible",
        True,
        {},
    ),
    (
        "data_manager.sources.sso.enabled",
        "data_manager.sources.sso.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.sso.visible",
        "data_manager.sources.sso.visible",
        True,
        {},
    ),
    (
        "data_manager.sources.indico.visible",
        "data_manager.sources.indico.visible",
        True,
        {},
    ),
    (
        "data_manager.sources.indico.sso_kwargs.headless",
        "data_manager.sources.indico.sso_kwargs.headless",
        True,
        {},
    ),
    (
        "data_manager.sources.jira.enabled",
        "data_manager.sources.jira.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.jira.visible",
        "data_manager.sources.jira.visible",
        True,
        {},
    ),
    (
        "data_manager.sources.redmine.enabled",
        "data_manager.sources.redmine.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.redmine.anonymize_data",
        "data_manager.sources.redmine.anonymize_data",
        True,
        {},
    ),
    (
        "data_manager.sources.elog.verify_ssl",
        "data_manager.sources.elog.verify_ssl",
        True,
        {},
    ),
    (
        "data_manager.processing.html_to_markdown.enabled",
        "data_manager.processing.html_to_markdown.enabled",
        True,
        {},
    ),
]

# Null-class rows: already honor an explicit false but render an explicit null
# as the string 'None'.  Same three-input structure as _BUG_ROWS.
_NULL_ROWS = [
    # :43 — inside {%- if services.benchmarking.anchors %}.  absent_kwargs
    # must make the enclosing if-block fire without supplying enabled itself.
    (
        "services.benchmarking.anchors.enabled",
        "services.benchmarking.anchors.enabled",
        True,
        {
            "services": {
                "benchmarking": {
                    "anchors": {"path": "examples/benchmarking/anchor_questions.json"}
                }
            }
        },
    ),
    (
        "services.chat_app.flask_debug_mode",
        "services.chat_app.flask_debug_mode",
        True,
        {},
    ),
    # :163 defaults to false — both absent and null should yield False.
    (
        "services.data_manager.auth.enabled",
        "services.data_manager.auth.enabled",
        False,
        {},
    ),
    (
        "services.grader_app.flask_debug_mode",
        "services.grader_app.flask_debug_mode",
        True,
        {},
    ),
    (
        "data_manager.retrievers.hierarchical_rerank.enabled",
        "data_manager.retrievers.hierarchical_rerank.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.indico.use_sso",
        "data_manager.sources.indico.use_sso",
        True,
        {},
    ),
    (
        "data_manager.sources.indico.slide_conversion.enabled",
        "data_manager.sources.indico.slide_conversion.enabled",
        True,
        {},
    ),
]

_ALL_ROWS = _BUG_ROWS + _NULL_ROWS


@pytest.mark.parametrize("input_path,rendered_path,default,absent_kwargs", _ALL_ROWS)
def test_false_renders_false(input_path, rendered_path, default, absent_kwargs):
    cfg = _render(**_expand(input_path, False))
    assert _get(cfg, rendered_path) is False


@pytest.mark.parametrize("input_path,rendered_path,default,absent_kwargs", _ALL_ROWS)
def test_absent_renders_default(input_path, rendered_path, default, absent_kwargs):
    cfg = _render(**absent_kwargs)
    assert _get(cfg, rendered_path) is default


@pytest.mark.parametrize("input_path,rendered_path,default,absent_kwargs", _ALL_ROWS)
def test_none_renders_default(input_path, rendered_path, default, absent_kwargs):
    cfg = _render(**_expand(input_path, None))
    assert _get(cfg, rendered_path) is default


def test_numeric_zero_not_replaced_by_default():
    # base_source_depth: explicit 0 must render as integer 0, not the default 1, not False
    cfg = _render(**_expand("data_manager.sources.links.base_source_depth", 0))
    val = _get(cfg, "data_manager.sources.links.base_source_depth")
    assert val == 0
    assert val is not False
    # absent -> default 1
    cfg_absent = _render()
    assert _get(cfg_absent, "data_manager.sources.links.base_source_depth") == 1

    # sitemap.max_pages: explicit 0 must render as integer 0, not the default 20000, not False
    cfg2 = _render(**_expand("data_manager.sources.links.sitemap.max_pages", 0))
    val2 = _get(cfg2, "data_manager.sources.links.sitemap.max_pages")
    assert val2 == 0
    assert val2 is not False
    # absent -> default 20000
    cfg_absent2 = _render()
    assert _get(cfg_absent2, "data_manager.sources.links.sitemap.max_pages") == 20000


# ---------------------------------------------------------------------------
# AST guard — keeps the fixed pattern from regressing
# ---------------------------------------------------------------------------


def _get_template_source():
    env = Environment(loader=PackageLoader("src.cli"))
    source, _, _ = env.loader.get_source(env, "base-config.yaml")
    return source


def _boolean_argument(node):
    """Return the filter's ``boolean`` argument, positional or keyword.

    ``default(7, true)`` and ``default(7, boolean=true)`` are the same call. Jinja
    parks the keyword spelling in ``node.kwargs``, so reading ``node.args`` alone
    both lets the deprecated form back in and rejects the one permitted form.
    """
    if len(node.args) >= 2:
        return node.args[1]
    for keyword in node.kwargs or []:
        if keyword.key == "boolean":
            return keyword.value
    return None


_UNREADABLE = object()


def _literal_default(node):
    """Resolve a ``default()`` first argument to its literal value.

    Returns ``_UNREADABLE`` when the expression is not a literal this walker models.

    Jinja does not parse ``-1`` as a ``Const``: it parses ``nodes.Neg`` wrapping
    ``Const(1)``. A predicate that only accepts ``Const`` therefore skips
    ``default(-1, true)`` -- a truthiness substitution -- while reporting green. Unary
    signs are folded here so both guards see one value instead of two node shapes.
    """
    if isinstance(node, nodes.Const):
        return node.value
    if isinstance(node, (nodes.Neg, nodes.Pos)):
        inner = _literal_default(node.node)
        if inner is _UNREADABLE or isinstance(inner, bool):
            # bool is an int subclass, so -true would fold to -1 and lose the fact
            # that the author wrote a boolean literal. Leave it to the other branch.
            return _UNREADABLE
        if not isinstance(inner, (int, float)):
            return _UNREADABLE
        return -inner if isinstance(node, nodes.Neg) else +inner
    return _UNREADABLE


def _walk_default_filters(source):
    """Walk the Jinja2 AST for ``| default(...)`` calls.

    Returns (bad_boolean_lines, truthy_non_bool_lines).
    bad_boolean_lines: lines where a boolean literal is used as default, except
        the permitted ``default(false, true)`` form.
    truthy_non_bool_lines: lines where a truthy non-boolean Const is used as
        default and true is the second argument.
    """
    ast = Environment().parse(source)
    bad_boolean = []
    truthy_non_bool = []

    for node in ast.find_all(nodes.Filter):
        if node.name != "default":
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        second_arg = _boolean_argument(node)

        value = _literal_default(first_arg)
        boolean_on = (
            second_arg is not None
            and isinstance(second_arg, nodes.Const)
            and second_arg.value is True
        )

        if isinstance(value, bool):
            is_allowed = value is False and boolean_on
            if not is_allowed:
                bad_boolean.append(node.lineno)
        elif value is not _UNREADABLE and bool(value) and boolean_on:
            truthy_non_bool.append(node.lineno)

    return bad_boolean, truthy_non_bool


def _call_site_path(node):
    """Return the dotted config path a filter is applied to, or None.

    A line number is not a usable identity for a call site — every edit above it
    renumbers the ones below. The config path is what the operator actually sets, and
    it survives the file being reordered.
    """
    parts = []
    current = node
    while True:
        if isinstance(current, nodes.Getattr):
            parts.append(current.attr)
            current = current.node
        elif isinstance(current, nodes.Getitem) and isinstance(
            current.arg, nodes.Const
        ):
            parts.append(str(current.arg.value))
            current = current.node
        elif isinstance(current, nodes.Name):
            parts.append(current.name)
            break
        else:
            return None
    return ".".join(reversed(parts))


def _default_filter_signatures(source):
    """Return a sorted ``path=default`` signature per frozen ``default(<truthy>, true)`` call."""
    ast = Environment().parse(source)
    signatures = []
    for node in ast.find_all(nodes.Filter):
        if node.name != "default" or not node.args:
            continue
        first_arg = node.args[0]
        second_arg = _boolean_argument(node)
        # Falsy non-boolean defaults are frozen alongside the truthy ones. They
        # cannot substitute a truthy value, so they are not the bug class — but
        # `default(0, true)` on a flag renders int 0 where the operator wrote
        # `false`, and 0 is not a boolean literal, so the other guard is blind to
        # it. Freezing every non-boolean literal leaves it nowhere to arrive.
        boolean_on = (
            second_arg is not None
            and isinstance(second_arg, nodes.Const)
            and second_arg.value is True
        )
        if not boolean_on:
            continue
        value = _literal_default(first_arg)
        if isinstance(value, bool) or value is _UNREADABLE:
            # Boolean literals belong to the other guard. _UNREADABLE here means a
            # non-scalar or computed default -- `default({}, true)`,
            # `default([], true)`, `default('a' if x else 'b', true)` -- which cannot
            # stand in for a boolean and is outside the bug class by the same argument
            # the module docstring makes for list defaults.
            continue
        path = _call_site_path(node.node)
        signatures.append(
            f"{path}={value!r}"
            if path is not None
            else f"<unresolved>@line{node.lineno}"
        )
    return sorted(signatures)


def test_guard_no_boolean_literal_defaults():
    """No default() call may use a boolean literal except the form default(false, true)."""
    source = _get_template_source()
    bad_lines, _ = _walk_default_filters(source)
    assert bad_lines == [], (
        f"Found default() calls with a boolean literal at lines {bad_lines}. "
        "Replace each with the ternary: "
        "{%- set v = <path> %}{{ v if v is defined and v is not none else <default> }}"
    )


# Frozen call sites for `default(<non-boolean literal>, true)`, truthy and falsy alike.
# The form may leave this list; nothing may join it. Removing a site means deleting its
# line here in the same commit.
_NON_BOOL_DEFAULT_BASELINE = [
    "collection_name='default_collection'",
    "data_manager.chunk_overlap=0",
    "data_manager.chunk_size=1000",
    "data_manager.chunking.strategy='sentence'",
    "data_manager.distance_metric='cosine'",
    "data_manager.embedding_class_map.HuggingFaceEmbeddings.kwargs.model_kwargs.device='cpu'",
    "data_manager.embedding_class_map.HuggingFaceEmbeddings.kwargs.model_name='sentence-transformers/all-MiniLM-L6-v2'",
    "data_manager.embedding_class_map.HuggingFaceEmbeddings.query_embedding_instructions='null'",
    "data_manager.embedding_class_map.HuggingFaceEmbeddings.similarity_score_reference=0.0",
    "data_manager.embedding_class_map.OpenAIEmbeddings.kwargs.model='text-embedding-3-small'",
    "data_manager.embedding_class_map.OpenAIEmbeddings.similarity_score_reference=0.0",
    "data_manager.embedding_name='OpenAIEmbeddings'",
    "data_manager.parallel_workers=32",
    "data_manager.processing.categorization.max_chars=4000",
    "data_manager.processing.categorization.max_concurrency=1",
    "data_manager.processing.categorization.model=''",
    "data_manager.processing.categorization.provider=''",
    "data_manager.retrievers.bm25_retriever.num_documents_to_retrieve=5",
    "data_manager.retrievers.hierarchical_rerank.candidate_pool_size=20",
    "data_manager.retrievers.hierarchical_rerank.num_documents_to_retrieve=5",
    "data_manager.retrievers.hierarchical_rerank.reranker.model='ms-marco-MiniLM-L-12-v2'",
    "data_manager.retrievers.hybrid_retriever.bm25_weight=0.6",
    "data_manager.retrievers.hybrid_retriever.num_documents_to_retrieve=5",
    "data_manager.retrievers.hybrid_retriever.semantic_weight=0.4",
    "data_manager.retrievers.semantic_retriever.num_documents_to_retrieve=5",
    "data_manager.sources.elog.schedule=''",
    "data_manager.sources.elog.url=''",
    "data_manager.sources.git.schedule=''",
    "data_manager.sources.indico.base_url='https://indico.cern.ch'",
    "data_manager.sources.indico.schedule=''",
    "data_manager.sources.indico.slide_conversion.llm_model=''",
    "data_manager.sources.indico.sso_kwargs.site_type='generic'",
    "data_manager.sources.jira.cutoff_date=''",
    "data_manager.sources.jira.max_tickets=10000000000.0",
    "data_manager.sources.jira.schedule=''",
    "data_manager.sources.jira.url=''",
    "data_manager.sources.links.schedule=''",
    "data_manager.sources.links.selenium_scraper.selenium_class='CERNSSOScraper'",
    "data_manager.sources.links.selenium_scraper.selenium_class_map.CERNSSOScraper.class='CERNSSOScraper'",
    "data_manager.sources.links.selenium_scraper.selenium_url='null'",
    "data_manager.sources.local_files.schedule=''",
    "data_manager.sources.redmine.schedule=''",
    "data_manager.sources.redmine.url=''",
    "data_manager.sources.sso.schedule=''",
    "data_manager.utils.anonymizer.nlp_model='en_core_web_sm'",
    "global.ACCOUNTS_PATH='/root/.accounts/'",
    "global.DATA_PATH='/root/data/'",
    "global.LOGGING.input_output_filename='chain_input_output.log'",
    "name='default'",
    "server_config.transport='streamable_http'",
    "server_config.url=''",
    "services.benchmarking.agent_class=''",
    "services.benchmarking.agent_md_file=''",
    "services.benchmarking.anchors.path='examples/benchmarking/anchor_questions.json'",
    "services.benchmarking.mode_settings.ragas_settings.embedding_model='OpenAI'",
    "services.benchmarking.mode_settings.ragas_settings.evaluator_model=''",
    "services.benchmarking.mode_settings.ragas_settings.evaluator_ollama_url=''",
    "services.benchmarking.mode_settings.ragas_settings.evaluator_provider=''",
    "services.benchmarking.mode_settings.ragas_settings.timeout=180",
    "services.benchmarking.mode_settings.sources_settings.default_match_field='file_name'",
    "services.benchmarking.model=''",
    "services.benchmarking.ollama_url=''",
    "services.benchmarking.out_dir='.'",
    "services.benchmarking.provider=''",
    "services.benchmarking.queries_path='queries'",
    "services.chat_app.agent_class='CMSCompOpsAgent'",
    "services.chat_app.agents_dir=''",
    "services.chat_app.auth.sso.authorize_url=''",
    "services.chat_app.auth.sso.client_kwargs.scope='openid profile email'",
    "services.chat_app.auth.sso.server_metadata_url=''",
    "services.chat_app.client_timeout_seconds=600",
    "services.chat_app.default_model='llama3.2'",
    "services.chat_app.default_provider='local'",
    "services.chat_app.evaluations.agent_config_path=None",
    "services.chat_app.evaluations.mcp_config_path=None",
    "services.chat_app.evaluations.root='/root/archi/evaluations'",
    "services.chat_app.external_port=7861",
    "services.chat_app.host='0.0.0.0'",
    "services.chat_app.hostname='localhost'",
    "services.chat_app.num_responses_until_feedback=3",
    "services.chat_app.port=7861",
    "services.chat_app.recursion_limit=50",
    "services.chat_app.skills_dir=''",
    "services.chat_app.static_folder='/root/archi/src/interfaces/chat_app/static'",
    "services.chat_app.template_folder='/root/archi/src/interfaces/chat_app/templates'",
    "services.chat_app.trained_on='No description provided.'",
    "services.data_manager.external_port=7871",
    "services.data_manager.host='0.0.0.0'",
    "services.data_manager.port=7871",
    "services.data_manager.static_folder='/root/archi/src/interfaces/chat_app/static'",
    "services.data_manager.template_folder='/root/archi/src/interfaces/chat_app/templates'",
    "services.grader_app.external_port=7862",
    "services.grader_app.host='0.0.0.0'",
    "services.grader_app.hostname='localhost'",
    "services.grader_app.num_problems=1",
    "services.grader_app.port=7861",
    "services.grader_app.template_folder='/root/archi/src/interfaces/grader_app/templates'",
    "services.grafana.external_port=3000",
    "services.grafana.port=3000",
    "services.mattermost.update_time=60",
    "services.piazza.agent_class='QAPipeline'",
    "services.piazza.update_time=60",
    "services.postgres.database='archi-db'",
    "services.postgres.port=5432",
    "services.postgres.user='archi'",
    "services.redmine_mailbox.agent_class='CMSCompOpsAgent'",
    "services.redmine_mailbox.agents_dir=''",
    "services.redmine_mailbox.answer_tag='-- archi -- Resolving email was sent'",
    "services.redmine_mailbox.imap4_port=143",
    "services.redmine_mailbox.mailbox_update_time=10",
    "services.redmine_mailbox.project=''",
    "services.redmine_mailbox.redmine_update_time=10",
    "services.redmine_mailbox.url=''",
    "services.vectorstore.distance_metric='cosine'",
    "utils.postgres.host='postgres'",
]


def test_guard_default_filter_baseline_is_unchanged():
    """The frozen call sites are exactly the ones recorded, name by name.

    A count alone cannot see a swap: convert one frozen site to the ternary, add the
    deprecated form at a different key, and 79 still equals 79.
    """
    signatures = _default_filter_signatures(_get_template_source())
    expected = sorted(_NON_BOOL_DEFAULT_BASELINE)
    added = sorted(set(signatures) - set(expected))
    removed = sorted(set(expected) - set(signatures))
    assert signatures == expected, (
        f"default(<truthy non-bool literal>, true) call sites drifted. "
        f"Added: {added or 'none'}. Removed: {removed or 'none'}. "
        "New boolean/numeric site: use the ternary "
        "({%- set v = <path> %}{{ v if v is defined and v is not none else <default> }}) "
        "rather than default(). Removed a converted site: delete its line from "
        "_NON_BOOL_DEFAULT_BASELINE in this same commit."
    )


# ---------------------------------------------------------------------------
# Nullable caps — a configured 0 must not become "unlimited"
# ---------------------------------------------------------------------------


def test_nullable_zero_caps_are_not_replaced_by_null():
    """0 means "fetch nothing"; null means "no cap". Rendering 0 as null inverts it.

    ``LinkScraper`` and ``ElogScraper`` both stop immediately at 0 and run unbounded
    at None, so a default() filter that swallows the 0 turns "no pages" into an
    unlimited crawl.
    """
    cfg = _render(**_expand("data_manager.sources.links.max_pages", 0))
    assert _get(cfg, "data_manager.sources.links.max_pages") == 0

    cfg_elog = _render(**_expand("data_manager.sources.elog.max_entries", 0))
    assert _get(cfg_elog, "data_manager.sources.elog.max_entries") == 0


def test_nullable_zero_caps_stay_null_when_absent():
    cfg = _render()
    assert _get(cfg, "data_manager.sources.links.max_pages") is None
    assert _get(cfg, "data_manager.sources.elog.max_entries") is None


def test_nullable_zero_caps_keep_a_configured_positive_value():
    cfg = _render(**_expand("data_manager.sources.links.max_pages", 250))
    assert _get(cfg, "data_manager.sources.links.max_pages") == 250

    cfg_elog = _render(**_expand("data_manager.sources.elog.max_entries", 250))
    assert _get(cfg_elog, "data_manager.sources.elog.max_entries") == 250


# ---------------------------------------------------------------------------
# The frozen baseline is a set of call sites, not a count
# ---------------------------------------------------------------------------


def test_guard_signature_baseline_matches_the_template():
    """Every frozen call site is still the one that was frozen."""
    assert _default_filter_signatures(_get_template_source()) == sorted(
        _NON_BOOL_DEFAULT_BASELINE
    )


def test_guard_detects_a_call_site_swapped_for_a_new_one():
    """A removal plus an addition leaves the count at 79 and must still fail.

    This is the hole a count-only baseline leaves: convert one frozen site to the
    ternary while adding the deprecated form at a different key, and the arithmetic
    balances. The signature comparison sees one path leave and another arrive.
    """
    source = _get_template_source()
    removed = "  distance_metric: {{ data_manager.distance_metric | default('cosine', true) }}"
    assert removed in source, "anchor line moved; update this test's fixture"
    swapped = source.replace(
        removed,
        "  {%- set v_dm_distance = data_manager.distance_metric %}\n"
        "  distance_metric: {{ v_dm_distance if v_dm_distance is defined "
        "and v_dm_distance is not none else 'cosine' }}\n"
        "  smuggled_in: {{ data_manager.smuggled_in | default(7, true) }}",
        1,
    )

    _, before = _walk_default_filters(source)
    _, after = _walk_default_filters(swapped)
    assert len(after) == len(
        before
    ), "fixture no longer reproduces the balanced swap the guard must catch"

    signatures = _default_filter_signatures(swapped)
    assert signatures != sorted(_NON_BOOL_DEFAULT_BASELINE)
    assert "data_manager.smuggled_in=7" in signatures
    assert "data_manager.distance_metric='cosine'" not in signatures


def test_guard_detects_a_new_zero_default():
    """`default(0, true)` on a flag renders int 0 where the operator wrote `false`.

    Round 1 froze only the truthy defaults, so a falsy non-boolean default could
    arrive without changing the baseline: it is not a boolean literal, so the other
    guard ignores it too. Every non-boolean literal default is frozen now.
    """
    source = _get_template_source()
    anchor = "  chunk_size: {{ data_manager.chunk_size | default(1000, true) }}"
    assert anchor in source, "anchor line moved; update this test's fixture"
    smuggled = source.replace(
        anchor,
        anchor
        + "\n  smuggled_flag: {{ data_manager.smuggled_flag | default(0, true) }}",
        1,
    )

    bad_lines, _ = _walk_default_filters(smuggled)
    assert (
        bad_lines == []
    ), "0 is not a boolean literal, so the other guard stays silent"

    signatures = _default_filter_signatures(smuggled)
    assert "data_manager.smuggled_flag=0" in signatures
    assert signatures != sorted(_NON_BOOL_DEFAULT_BASELINE)


def test_guard_freezes_the_falsy_scalar_defaults_too():
    """The zero and empty-string defaults already in the file are frozen.

    Scalar literals only. A `default([], true)` parses to a Jinja List node rather
    than a Const, so it is outside this guard — and outside the bug class too, since
    a list default can never stand in for a boolean.
    """
    signatures = _default_filter_signatures(_get_template_source())
    assert "data_manager.chunk_overlap=0" in signatures
    assert (
        "data_manager.embedding_class_map.HuggingFaceEmbeddings."
        "similarity_score_reference=0.0" in signatures
    )
    assert any(signature.endswith("=''") for signature in signatures)


def test_guard_sees_the_keyword_boolean_form():
    """`default(7, boolean=true)` is the same call, written differently.

    Jinja stores the second argument in ``node.kwargs`` for the keyword spelling, so
    a walker that reads ``node.args`` alone lets the deprecated form back in under a
    new name — and rejects the one permitted form, ``default(false, boolean=true)``,
    for the same reason.
    """
    source = _get_template_source()
    anchor = "  chunk_size: {{ data_manager.chunk_size | default(1000, true) }}"
    assert anchor in source, "anchor line moved; update this test's fixture"
    smuggled = source.replace(
        anchor,
        anchor
        + "\n  kw_smuggled: {{ data_manager.kw_smuggled | default(7, boolean=true) }}"
        + "\n  kw_flag: {{ data_manager.kw_flag | default(true, boolean=true) }}",
        1,
    )

    signatures = _default_filter_signatures(smuggled)
    assert "data_manager.kw_smuggled=7" in signatures

    bad_lines, _ = _walk_default_filters(smuggled)
    assert bad_lines, "a boolean-literal default in keyword form must still be rejected"


def test_guard_permits_the_keyword_spelling_of_the_allowed_form():
    """``default(false, boolean=true)`` is the one permitted boolean use, in either spelling."""
    source = _get_template_source()
    anchor = "  chunk_size: {{ data_manager.chunk_size | default(1000, true) }}"
    rewritten = source.replace(
        anchor,
        anchor + "\n  kw_ok: {{ data_manager.kw_ok | default(false, boolean=true) }}",
        1,
    )
    bad_lines, _ = _walk_default_filters(rewritten)
    assert bad_lines == []


def test_guard_sees_a_negative_literal_default():
    """``default(-1, true)`` is a truthiness substitution both guards used to miss.

    Jinja parses ``-1`` as ``nodes.Neg`` wrapping a ``Const``, not as a ``Const``. Both
    predicates tested ``isinstance(first_arg, nodes.Const)``, so a future
    meaningful-zero field written this way would replace a configured ``0`` with ``-1``
    while every advertised guard stayed green -- the exact form this change exists to
    keep out, readmitted under a unary minus.
    """
    source = _get_template_source()
    anchor = "  chunk_size: {{ data_manager.chunk_size | default(1000, true) }}"
    assert anchor in source, "anchor line moved; update this test's fixture"
    smuggled = source.replace(
        anchor,
        anchor
        + "\n  neg_smuggled: {{ data_manager.neg_smuggled | default(-1, true) }}",
        1,
    )

    signatures = _default_filter_signatures(smuggled)
    assert "data_manager.neg_smuggled=-1" in signatures
    assert signatures != sorted(_NON_BOOL_DEFAULT_BASELINE)


@pytest.mark.parametrize(
    "path", ["global.ACCEPTED_FILES", "services.benchmarking.modes"]
)
def test_null_on_an_iterated_container_key_fails_the_render(path):
    """Pins the two keys where the null guarantee does not hold.

    Both take a bare ``default([...])``, which replaces only an *undefined* value, and
    both are iterated directly by a ``{%- for %}``. So an explicit ``null`` reaches the
    loop and raises rather than falling back to the default -- and `archi create`
    cannot render the config at all.

    Documented in `docs/docs/configuration.md` rather than fixed: converting these is
    a separate change with its own blast radius, and the doc claim needs a test or it
    silently rots. If a later change adds ``boolean=true`` here, this test fails and
    the documented exception comes out with it.
    """
    with pytest.raises(TypeError, match="not iterable"):
        _render(**_expand(path, None))


def test_null_on_a_boolean_flagged_default_still_yields_the_default():
    """The contrast case, so the exception above reads as narrow rather than general."""
    cfg = _render(**_expand("global.DATA_PATH", None))
    assert cfg["global"]["DATA_PATH"] == "/root/data/"


def test_guard_folds_a_signed_literal_without_losing_the_boolean_check():
    """Folding unary signs must not swallow a boolean literal.

    ``bool`` is an ``int`` subclass, so a naive fold turns ``default(-true, true)``
    into ``-1`` and reports a truthy non-boolean -- moving a boolean-literal default
    out of the guard that exists to reject it. The fold declines booleans instead, so
    the boolean guard still sees the call.
    """
    source = _get_template_source()
    anchor = "  chunk_size: {{ data_manager.chunk_size | default(1000, true) }}"
    smuggled = source.replace(
        anchor,
        anchor
        + "\n  signed_bool: {{ data_manager.signed_bool | default(-true, true) }}",
        1,
    )
    signatures = _default_filter_signatures(smuggled)
    assert not any("signed_bool" in signature for signature in signatures)


def test_guard_leaves_container_and_computed_defaults_out_of_scope():
    """Non-scalar and computed defaults stay outside both guards, on purpose.

    ``default({}, true)``, ``default([], true)`` and
    ``default('a' if flag else 'b', true)`` are all in the template today. None can
    stand in for a boolean, so none is in the bug class -- the same argument the module
    docstring already makes for list defaults. Pinned here so the boundary is enforced
    rather than only asserted in a comment, and so widening the walker to fail on every
    shape it cannot read does not land silently: that reads as a fix and is really a
    dozen false failures on lines that were always fine.
    """
    source = _get_template_source()
    bad_lines, _ = _walk_default_filters(source)
    assert bad_lines == []

    for fixture in (
        "  dict_default: {{ data_manager.dict_default | default({}, true) }}",
        "  list_default: {{ data_manager.list_default | default([], true) }}",
        "  cond_default: "
        "{{ data_manager.cond_default | default('a' if verbosity else 'b', true) }}",
    ):
        smuggled = source + "\n" + fixture
        smuggled_bad, _ = _walk_default_filters(smuggled)
        assert smuggled_bad == [], f"{fixture!r} must not be reported as a bad boolean"
        assert _default_filter_signatures(smuggled) == sorted(
            _NON_BOOL_DEFAULT_BASELINE
        ), f"{fixture!r} must not join the frozen baseline"
