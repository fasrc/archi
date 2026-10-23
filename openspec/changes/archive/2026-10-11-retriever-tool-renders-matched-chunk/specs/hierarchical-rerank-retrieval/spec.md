## ADDED Requirements

### Requirement: Returned parents record the span of the child that ranked them
For each parent document it returns, the hierarchical retriever SHALL record the character span of
the best-ranked child chunk within the parent's `page_content` as integer metadata `matched_start`
and `matched_end`, such that `page_content[matched_start:matched_end]` equals that child's text. The
retriever MUST NOT record a span it cannot locate, and MUST NOT change `page_content`.

#### Scenario: Span locates the matched child
- **WHEN** a child chunk whose text occurs at character offset S of its parent's text ranks the parent
- **THEN** the returned parent document has `matched_start == S` and
  `matched_end == S + len(child_text)`, and its `page_content` is the full parent text

#### Scenario: Best-ranked child wins when children share a parent
- **WHEN** two children of the same parent are in the candidate pool and the cross-encoder ranks
  child B above child A
- **THEN** the parent appears once, and its span locates child B

#### Scenario: Child text not found in the parent
- **WHEN** the best-ranked child's text does not occur in its parent's text
- **THEN** the returned parent document has neither `matched_start` nor `matched_end`

#### Scenario: Child without a parent passes through unchanged
- **WHEN** a candidate without a `parent_id` is returned as its own document
- **THEN** that document has neither `matched_start` nor `matched_end`
