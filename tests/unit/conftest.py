"""Shared setup for the unit test suite.

Pin the real ``langchain_core`` package into ``sys.modules`` before any test module
is collected. Several vectorstore tests install bare ``langchain_core`` *stubs* (so
they can import the manager without the heavy real dependency when it is absent); if
one of those modules is collected before a module that needs the real
``langchain_core.messages`` (e.g. the categorization tests), the stub shadows the real
package and the genuine import fails — a collection-order-dependent flake.

Importing the real package here (conftest runs before collection) guarantees it wins
when it is actually installed, making the suite order-independent. When langchain is
genuinely absent the import is skipped and the stubbing tests behave exactly as before.
"""

try:  # pragma: no cover - environment-dependent
    import langchain_core  # noqa: F401
    import langchain_core.messages  # noqa: F401
except Exception:  # pragma: no cover - langchain not installed; stubs take over
    pass
