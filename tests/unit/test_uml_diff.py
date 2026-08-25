"""Unit tests for the PR UML-diff generator `scripts/ci/uml_diff.py`.

The parser fixtures are verbatim `pyreverse -o mmd -f ALL` output (pylint
4.0.7) for a small sample module pair, so the parser is pinned to the real
emitter format: two-space indent, `class Name {` blocks, dunder underscores
escaped as `\\_\\_`, no visibility signs, and relation arrows such as
`Dog --|> Animal`.

The script feeds a PR comment, so the emitter contract matters: a hidden
HTML marker on the first line (the workflow finds its sticky comment by
it), GitHub diff colors in `classDef` lines, `:::` style tags per class,
and member-level markers in front of the member (a trailing marker would
render as a Mermaid return type).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "uml_diff.py"
_spec = importlib.util.spec_from_file_location("uml_diff", _SCRIPT)
assert _spec is not None and _spec.loader is not None
uml_diff = importlib.util.module_from_spec(_spec)
# Registration must come first: dataclass field-type resolution looks the
# module up in sys.modules while the module body runs.
sys.modules[_spec.name] = uml_diff
_spec.loader.exec_module(uml_diff)

BASE_MMD = """classDiagram
  class Animal {
    legs : int
    name
    \\_\\_init\\_\\_(name)
    \\_\\_repr\\_\\_()
    speak()
  }
  class Dog {
    bark()
  }
  class Obsolete {
    retire()
  }
  Dog --|> Animal
"""

HEAD_MMD = """classDiagram
  class Animal {
    legs : int
    name
    \\_\\_init\\_\\_(name)
    \\_\\_repr\\_\\_()
    eat(food)
    speak()
  }
  class Cat {
    lives : int
    \\_\\_init\\_\\_(name, lives)
    meow()
  }
  class Dog {
    bark()
  }
  Cat --|> Animal
  Dog --|> Animal
"""


def _diff(base_text: str, head_text: str):
    return uml_diff.diff_models(
        uml_diff.parse_mmd(base_text), uml_diff.parse_mmd(head_text)
    )


class TestParse:
    def test_classes_members_and_relations(self):
        model = uml_diff.parse_mmd(BASE_MMD)
        assert set(model.classes) == {"Animal", "Dog", "Obsolete"}
        assert "legs : int" in model.classes["Animal"]
        assert "\\_\\_init\\_\\_(name)" in model.classes["Animal"]
        assert model.classes["Dog"] == ["bark()"]
        assert ("Dog", "--|>", "Animal") in model.relations

    def test_empty_text_gives_empty_model(self):
        model = uml_diff.parse_mmd("")
        assert model.classes == {}
        assert model.relations == set()

    def test_class_without_braces(self):
        model = uml_diff.parse_mmd("classDiagram\n  class Bare\n")
        assert model.classes == {"Bare": []}


class TestDiff:
    def test_classification(self):
        d = _diff(BASE_MMD, HEAD_MMD)
        assert set(d.added) == {"Cat"}
        assert set(d.removed) == {"Obsolete"}
        assert set(d.modified) == {"Animal"}
        assert d.unchanged == ["Dog"]
        assert d.modified["Animal"].added_members == ["eat(food)"]
        assert d.modified["Animal"].removed_members == []

    def test_relation_sets(self):
        d = _diff(BASE_MMD, HEAD_MMD)
        assert ("Cat", "--|>", "Animal") in d.added_relations
        assert ("Dog", "--|>", "Animal") in d.kept_relations
        assert d.removed_relations == set()

    def test_identical_models_are_empty(self):
        d = _diff(BASE_MMD, BASE_MMD)
        assert d.is_empty


class TestRender:
    def _md(self):
        return uml_diff.render_markdown(_diff(BASE_MMD, HEAD_MMD))

    def test_marker_is_first_line(self):
        assert self._md().startswith(uml_diff.MARKER)

    def test_added_class_is_styled_and_full(self):
        md = self._md()
        assert "class Cat:::added" in md
        assert "meow()" in md

    def test_removed_class_is_styled_and_full(self):
        md = self._md()
        assert "class Obsolete:::removed" in md
        assert "retire()" in md

    def test_modified_class_marks_new_member_with_prefix(self):
        md = self._md()
        assert "class Animal:::modified" in md
        assert "➕eat(food)" in md

    def test_unchanged_dunder_is_hidden_but_init_kept(self):
        md = self._md()
        assert "\\_\\_repr\\_\\_" not in md
        assert "\\_\\_init\\_\\_(name)" in md
        assert "speak()" in md

    def test_unchanged_class_is_name_only_context(self):
        md = self._md()
        assert "class Dog:::unchanged" in md
        assert "bark()" not in md

    def test_relations_and_classdefs_present(self):
        md = self._md()
        assert "Cat --|> Animal" in md
        assert "Dog --|> Animal" in md
        assert "classDef added fill:#dafbe1,stroke:#1a7f37" in md
        assert "classDef removed fill:#ffebe9,stroke:#cf222e" in md
        assert "classDef modified fill:#fff8c5,stroke:#9a6700" in md
        assert "classDef unchanged fill:#f6f8fa,stroke:#d0d7de" in md

    def test_removed_member_marked_in_modified_class(self):
        base = "classDiagram\n  class A {\n    run()\n    walk()\n  }\n"
        head = "classDiagram\n  class A {\n    run()\n  }\n"
        md = uml_diff.render_markdown(_diff(base, head))
        assert "class A:::modified" in md
        assert "➖walk()" in md
        assert "run()" in md

    def test_removed_class_keeps_its_base_relation(self):
        base = "classDiagram\n  class A {\n  }\n  class B {\n    b()\n  }\n  B --|> A\n"
        head = "classDiagram\n  class A {\n  }\n"
        md = uml_diff.render_markdown(_diff(base, head))
        assert "class B:::removed" in md
        assert "B --|> A" in md

    def test_no_structural_change_returns_none(self):
        assert uml_diff.render_markdown(_diff(BASE_MMD, BASE_MMD)) is None


def _bulk(n: int, extra: str = "") -> str:
    blocks = "".join(f"  class Unch{i} {{\n    m{i}()\n  }}\n" for i in range(n))
    return "classDiagram\n" + blocks + extra


class TestSizeGuard:
    def test_drops_unchanged_context_over_the_cap(self):
        base = _bulk(40)
        head = _bulk(40, "  class Fresh {\n    f()\n  }\n")
        md = uml_diff.render_markdown(_diff(base, head), max_chars=700)
        assert "class Fresh:::added" in md
        assert "class Unch0" not in md
        assert "hidden" in md

    def test_falls_back_to_summary_table_when_still_over(self):
        base = _bulk(40)
        head = _bulk(40, "  class Fresh {\n    f()\n  }\n")
        md = uml_diff.render_markdown(_diff(base, head), max_chars=50)
        assert md.startswith(uml_diff.MARKER)
        assert "```mermaid" not in md
        assert "| Fresh | added |" in md


class TestMain:
    def test_writes_file_and_exits_zero(self, tmp_path):
        b = tmp_path / "b.mmd"
        h = tmp_path / "h.mmd"
        out = tmp_path / "uml-diff.md"
        b.write_text(BASE_MMD)
        h.write_text(HEAD_MMD)
        rc = uml_diff.main(["--base", str(b), "--head", str(h), "--out", str(out)])
        assert rc == 0
        assert out.read_text().startswith(uml_diff.MARKER)

    def test_missing_base_file_means_all_added(self, tmp_path):
        h = tmp_path / "h.mmd"
        out = tmp_path / "uml-diff.md"
        h.write_text(HEAD_MMD)
        rc = uml_diff.main(
            [
                "--base",
                str(tmp_path / "absent.mmd"),
                "--head",
                str(h),
                "--out",
                str(out),
            ]
        )
        assert rc == 0
        assert "class Animal:::added" in out.read_text()

    def test_no_change_exits_three_and_writes_nothing(self, tmp_path):
        b = tmp_path / "b.mmd"
        out = tmp_path / "uml-diff.md"
        b.write_text(BASE_MMD)
        rc = uml_diff.main(["--base", str(b), "--head", str(b), "--out", str(out)])
        assert rc == 3
        assert not out.exists()
