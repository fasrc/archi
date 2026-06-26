# Design

## Context

`HtmlToMarkdownProcessor.process` (`src/data_manager/collectors/processing.py`, ~line 85)
calls `markdownify(content, heading_style="ATX")` at line 97 inside a `try/except` that,
on any failure, logs and returns the original resource. `markdownify==1.2.2` recurses the
parse tree; deep trees overflow Python's recursion limit (default 1000).

## Goals / Non-goals

- **Goal:** deeply-nested HTML converts to Markdown instead of falling back to raw HTML.
- **Goal:** no process crash. Raising `sys.setrecursionlimit` alone can overflow the C
  stack and **segfault** — must be bounded and/or paired with a larger thread stack.
- **Non-goal:** changing the fallback contract (exception/blank → original resource).
- **Non-goal:** new dependencies; stdlib only.

## Decision

Run the conversion in a **worker thread created with an enlarged
`threading.stack_size(...)`**, and inside that thread set a **bounded**
`sys.setrecursionlimit(max(current, N))`; capture the result/exception and join. The
larger native stack makes a deeper Python recursion safe; bounding the limit avoids an
unbounded blow-up. Restore any process-global recursion limit in a `finally`.

If a simpler bounded `sys.setrecursionlimit(max(current, 10000))` around the call (restored
in `finally`) is verified not to segfault on the deep fixture, it is acceptable — but the
thread-stack approach is preferred for safety.

The existing `if not markdown or not markdown.strip()` blank-guard and the `except`
raise-guard remain: conversion that still fails or yields blank → return original resource.

## Risks

- Segfault from an over-raised limit on a small native stack → mitigated by the worker
  thread's enlarged `stack_size` and a bounded limit.
- A global `sys.setrecursionlimit` change leaking to other code → mitigated by `finally`
  restore (and the worker thread is short-lived).
