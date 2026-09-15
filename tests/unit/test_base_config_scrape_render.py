"""The base-config template must let explicit scrape-concurrency values through.

`scrape_workers` / `scrape_per_host_workers` are documented as "clamped to a
minimum of 1", so an operator writing `0` is asking for the sequential path.
Jinja's two-argument `default(x, true)` replaces every *falsey* value, not just
undefined ones, so a configured `0` would be silently rendered as the shipped
default (8 or 4) and the runtime clamp would never see it — the deployment would
run at eight times the requested global concurrency.

`min_pages` in the same template already uses the undefined-only form for exactly
this reason (`test_base_config_sitemap_render.py`); these two knobs must match it.
"""

from pathlib import Path

import yaml
from jinja2 import ChainableUndefined, Environment, PackageLoader, select_autoescape


def _render(data_manager):
    # Mirror the env in src/cli/cli_main.py (PackageLoader + ChainableUndefined).
    env = Environment(
        loader=PackageLoader("src.cli"),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    template = env.get_template("base-config.yaml")
    rendered = template.render(verbosity=0, data_manager=data_manager)
    return yaml.safe_load(rendered)


def _dm(cfg):
    return cfg["data_manager"]


def test_defaults_when_unset():
    dm = _dm(_render({}))
    assert dm["scrape_workers"] == 8
    assert dm["scrape_per_host_workers"] == 4


def test_explicit_values_are_rendered():
    dm = _dm(_render({"scrape_workers": 12, "scrape_per_host_workers": 2}))
    assert dm["scrape_workers"] == 12
    assert dm["scrape_per_host_workers"] == 2


def test_explicit_zero_reaches_the_runtime_clamp():
    dm = _dm(_render({"scrape_workers": 0, "scrape_per_host_workers": 0}))
    assert dm["scrape_workers"] == 0
    assert dm["scrape_per_host_workers"] == 0


def test_negative_values_reach_the_runtime_clamp():
    dm = _dm(_render({"scrape_workers": -3, "scrape_per_host_workers": -1}))
    assert dm["scrape_workers"] == -3
    assert dm["scrape_per_host_workers"] == -1


def test_explicit_null_falls_back_to_the_default():
    # `scrape_workers:` with no value is "unset", not "zero".
    dm = _dm(_render({"scrape_workers": None, "scrape_per_host_workers": None}))
    assert dm["scrape_workers"] == 8
    assert dm["scrape_per_host_workers"] == 4


def test_generated_config_and_docs_give_the_same_db_tuning_advice():
    """The template's tuning note must not contradict `docs/docs/configuration.md`.

    `PersistenceService` writes through `PostgresCatalogService.upsert_resource()`,
    which opens a fresh `psycopg2.connect()` per write; it never touches the pool in
    `src/utils/connection_pool.py`. Telling an operator to raise that pool cannot
    affect the scrape phase — the real ceiling is the server's `max_connections`.
    The docs were corrected; the rendered template is the copy an operator actually
    edits, so the two have to say the same thing.
    """
    env = Environment(
        loader=PackageLoader("src.cli"),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    source = env.loader.get_source(env, "base-config.yaml")[0]
    scrape_note = source.split("# Scrape-phase concurrency", 1)[1].split(
        "reset_collection:", 1
    )[0]
    docs = Path("docs/docs/configuration.md").read_text()

    # The superseded instruction is gone from both.
    assert "raising that pool in tandem" not in scrape_note
    assert "raising that pool in tandem" not in docs
    # ...and both name the limit that actually binds.
    assert "max_connections" in scrape_note
    assert "max_connections" in docs
