"""Tokenize and parse catalog-search queries.

Lives outside ``src/interfaces/uploader_app/app.py`` so it can be unit tested:
importing that module pulls heavy optional deps (jira, nltk, llama_index) that
are absent from the test environment.

Query grammar: whitespace-separated tokens, where ``key:value`` becomes a
metadata filter, a bare ``OR`` starts a new filter group, and everything else is
free text. ``"`` groups a phrase (``key:"two words"``). ``'`` is a literal
character so English contractions survive.
"""

from __future__ import annotations

import shlex
from typing import Dict, List, Tuple

METADATA_ALIAS_MAP = {
    "resource_type": "source_type",
    "resource_id": "ticket_id",
}


def tokenize_query(query: str) -> List[str]:
    """Split a raw user query into tokens.

    ``shlex.split`` cannot be used directly: it treats ``'`` as a quote
    character, so ``can't`` raises ``ValueError`` and ``don't ... user's``
    silently fuses the words between the two apostrophes into one token.
    """
    lexer = shlex.shlex(query, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.quotes = '"'
    try:
        return list(lexer)
    except ValueError:
        # Unbalanced double quote. Degrade to a plain split rather than 500.
        return query.split()


def parse_metadata_query(
    query: str,
) -> Tuple[Dict[str, str] | List[Dict[str, str]], str]:
    filters_groups: List[Dict[str, str]] = []
    current_group: Dict[str, str] = {}
    free_tokens: List[str] = []
    for token in tokenize_query(query):
        if token.upper() == "OR":
            if current_group:
                filters_groups.append(current_group)
                current_group = {}
            continue
        if ":" in token:
            key, value = token.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key and value:
                key = METADATA_ALIAS_MAP.get(key, key)
                current_group[key] = value
                continue
        free_tokens.append(token)

    if current_group:
        filters_groups.append(current_group)

    if not filters_groups:
        filters: Dict[str, str] | List[Dict[str, str]] = {}
    elif len(filters_groups) == 1:
        filters = filters_groups[0]
    else:
        filters = filters_groups

    return filters, " ".join(free_tokens)
