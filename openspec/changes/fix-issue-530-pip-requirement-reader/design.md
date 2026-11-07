# Design — read requirement lines as pip does

Every verdict below was measured at `5564e016` (Python 3.11.15, pip 26.1.2, packaging 26.2).
Where the issue body and the measurement disagree, the measurement wins and the difference is
stated.

`prototype.md` in this directory is the reader that was measured. Appended to a copy of
the module, it passes all **254** existing tests and agrees with pip on every acceptance row
except the four deliberate stricter-than-pip rows (D6). It is a reference, not a spec: copy
its logic, not its layout. It is Markdown so that black and pytest do not collect it.

## D1. The option half: pip's `break_args_options`, then `shlex`, then a closed table

pip 26.1.2, `pip._internal.req.req_file.break_args_options`:

```python
tokens = line.split(" ")
args = []
options = tokens[:]
for token in tokens:
    if token.startswith(("-", "--")):
        break
    else:
        args.append(token)
        options.pop(0)
return " ".join(args), " ".join(options)
```

The guard copies this split exactly (a literal space, not `\s`; this keeps
`test_an_option_not_preceded_by_a_space_fails_closed` green). Then `shlex.split(options)`; a
`ValueError` (an open quote) means the option half is rejected.

Each token is read in order:

| Token | Verdict |
|---|---|
| does not start with `-` | **ignored** — see below |
| `--hash=<v>`, or `--hash` then `<v>` as the next token | accepted if `<v>` has a `:` and the part before it is `sha256`, `sha384` or `sha512`; else rejected |
| `-C` or `--config-settings`, then `<v>` as the next token | accepted if `<v>` contains `=`; else (or no next token) rejected |
| `--config-settings=<v>` | accepted if `<v>` contains `=` |
| `-C<v>` | accepted if `<v>` contains `=` (so `-C=` is accepted: pip records the empty key) |
| any other token that starts with `-` | rejected |

**Correction to the issue body.** The issue's table lists `--hash=…` only. Measured:
`vllm==0.9.0 --hash sha256:aa` is accepted by pip, and the existing test
`test_a_no_break_space_inside_an_option_value_is_still_cut[vllm==0.9.0 --hash\tsha256:aa]`
requires the spaced form to be read. The table therefore accepts `--hash <v>` as well.

**Correction to the issue body.** A non-dash token in the option half is ignored, not
rejected. optparse keeps it as a positional argument and pip ignores it. Measured:
`vllm==0.9.0 --hash=sha256:aa ; python_version < "3.11"` is **accepted** by pip, with no marker.
The existing test `test_a_semicolon_inside_a_per_requirement_option_is_not_a_marker` requires
that verdict. Rejecting non-dash tokens fails that test.

**The algorithm check is new and is pip's.** `--hash=md5:aa` is rejected by pip
(`Allowed hash algorithms for --hash are sha256, sha384, sha512.`), and `--hash=sha256` is
rejected (`must be a hash name followed by a value`). The hex value is not checked; pip does
not check it at parse time either.

If the option half is accepted, the line becomes the requirement half (`args`, stripped). If
it is rejected, the **whole line stays as it is**. The requirement reader (D2) then reports it
(D2 step 5 — it does **not** rely on `packaging` failing), so no pin is read. This keeps
`test_bare_trailing_config_settings_flag_is_not_cut` green: `_requirement_lines("vllm==0.9.0 -C")`
still yields `"vllm==0.9.0 -C"`.

## D2. The requirement half, in pip's `_get_url_from_path` order

`_requirement_lines` keeps its contract: join lines, cut comments (`_COMMENT`), skip blanks
and whole-line options, apply D1. It no longer cuts at `;`; the marker belongs to the
requirement and `packaging` reads it. Readers that want only the name and specifier cut the
marker themselves.

A single private reader takes one line from `_requirement_lines` and returns
`(name, requirement_or_None, reported)`:

1. `_ENVIRONMENT_SUBSTITUTION` matches → reported.
2. `_VCS_OR_URL_REQUIREMENT` matches at the start → reported.
3. A leading `.`, or a `/` or `\` before any `;` or `@` → reported (a local path).
4. Cut the marker (`;…`), strip a trailing `[…]`. If what is left ends in `_ARCHIVE_SUFFIX`
   → reported, **unless** the line is `name [extras] @ <scheme>…` with a scheme from
   `_VCS_OR_URL_REQUIREMENT` (the guard's D4: a named direct reference stays readable and
   `_unpinned_protected` fails it closed).
5. Run `_break_args_options(line)` again. A non-empty option half here means D1 rejected it
   in `_requirement_lines` → reported. **Do not rely on `packaging` to fail on such a line.**
   Refuter finding (2026-10-08, measured): in `vllm==0.9.0; os_name == "a -Cbad"` the split
   opens the option half inside the quoted marker value, `shlex` raises on the open quote, D1
   rejects, and the whole line stays — but `packaging` parses the whole line as a valid
   requirement with a marker and an earlier prototype recorded the pin. pip refuses the line
   (`Could not split options: -Cbad"`). The same holds for `vllm==0.9.0 ; os_name == "x --bogus"`,
   `vllm==0.9.0; os_name == 'a -x'` and `vllm==0.9.0 ; os_name == "a -Cfoo=bar"`.
6. Split at the first `;`, as pip's `parse_req_from_line` does for a line that is not a URL
   (a URL line was reported at step 2). `Requirement(head.strip())`; the marker is
   `packaging.markers.Marker(rest.strip())` only when `rest.strip()` is non-empty, else no
   marker. `InvalidRequirement` or `InvalidMarker` → reported. Refuter finding (2026-10-08,
   measured): `Requirement("vllm==0.9.0 ;")` raises, but pip accepts `vllm==0.9.0 ;` and
   `sympy==1.13.1;` as unconditional pins, and the code at `5564e016` reads them as pins.
   Passing the whole line to `Requirement` would be a regression and a fifth undocumented
   stricter-than-pip row. The split also makes `vllm @ https://h/x.whl ; python_version<"3"`
   carry its marker, as pip reads it.
7. `req.url` set and the URL has no scheme from `_VCS_OR_URL_REQUIREMENT` → reported
   (`evil@../pkgs/vllm` stays reported, guard D4).

The name is `_normalize_name(req.name)`. When there is no parsed requirement, the name is a
best-effort leading `[A-Za-z0-9][A-Za-z0-9._-]*`. That name is used **only** to keep a
protected package visible to `_parse_requirements`, `_declarations` and `_unpinned_protected`.
It never produces a pin. This keeps `test_an_option_not_preceded_by_a_space_fails_closed`
green, which asserts `"vllm" in _unpinned_protected("vllm==0.9.0\t-Cfoo=bar")`: that line is
`InvalidRequirement`, and without the fallback vllm would read as absent — a silent skip.

## D3. The pin, the specifier text and the marker

- **Pin:** only when the line is not reported, `req.url` is `None`, `req.specifier` has
  exactly one clause, its operator is `==` or `===`, and its version has no `*`. Otherwise the
  line is unpinned.
  - `vllm == 0.9.0` and `vllm (==0.9.0)` now read as pins. pip accepts both; today's anchored
    `_PIN_PATTERN` reads them as unpinned. This is a change toward pip, in the safe direction
    for the guards (a pin is checked, an unpinned name fails the suite).
  - `vllm==1.0.*` reads as unpinned. pip accepts it, but it is not one version; today the
    module records `1.0.*` and `Version("1.0.*")` raises downstream.
- **Specifier text** for `_parse_requirements` and `_declarations` is the **source text**
  after the name and an optional extras list, cut at `;` and stripped — not
  `str(req.specifier)`. Measured: `str(SpecifierSet(">1.13.0,<2"))` is `"<2,>1.13.0"`, which
  fails `test_a_range_on_a_protected_package_is_not_read_as_absent[sympy>1.13.0,<2-expected2]`.
- **Marker** for `_conditional_protected` is `str(marker)` from D2 step 6. When the line did not parse
  but the best-effort name is protected and the line has a `;`, report the text after the
  first `;` — fail loud rather than drop it. All existing marker tests pass with this rule.

## D4. Order of the two commits

Commit A (the option half) alone is green: measured, 254 passed, and the three rejected
option rows record no pin while `-Cfoo"="bar` records `{'vllm': '0.9.0'}`. In commit A,
`_PIN_PATTERN` and `_REQUIREMENT_PATTERN` still exist, so because `_requirement_lines` no longer
cuts `;`, every reader that matches those patterns must cut the marker itself first
(`line.split(";", 1)[0].strip()`). `_conditional_protected` iterates `_requirement_lines` and
partitions on `;` itself. In commit A, the rejected option rows are unpinned but not yet
reported by `_opaque_requirements`; commit B reports them.

Commit B adds the reader of D2 and D3, routes all five readers through it, and deletes the
constants.

## D5. Constants

Deleted in commit B (acceptance criterion): `_PIN_PATTERN`, `_REQUIREMENT_PATTERN`,
`_COMPARISON_AFTER_SUFFIX`, `_ATTACHED_EXTRAS`, `_EXTRA_NAME`, `_VALID_EXTRAS_LIST`,
`_NAME_WITH_SPACED_EXTRAS`, `_NAMED_URL_REFERENCE`, `_PATH_OR_ARCHIVE_REQUIREMENT`.
`_PER_REQUIREMENT_OPTION` and `_CONFIG_SETTING_VALUE` are replaced by the D1 helper in commit A
and deleted when nothing reads them. `_NAME` and `_WSP` go when nothing reads them.

Kept: `_COMMENT`, `_ARCHIVE_SUFFIX`, `_VCS_OR_URL_REQUIREMENT`, `_ENVIRONMENT_SUBSTITUTION`,
`_joined_lines` (#529 owns it), `_REQUIREMENT_BEARING_PATTERN`.

Long comments above deleted constants carry the review history. Move the pip rules they
record (the measured verdicts and review dates) into the docstrings of the D1 helper and the
D2 reader in a shorter form. Do not drop the measured facts.

## D6. The four stricter-than-pip rows

Each row is pinned by a test whose docstring or comment contains `stricter than pip`:

| Line | pip | Guard | Why |
|---|---|---|---|
| `vllm-0.9.0-py3-none-any.whl[foo]` | accepts | reported | guard contract: no name is read from an archive filename |
| `evil@../pkgs/vllm` | accepts | reported | guard D4: a local path wearing a name |
| `vllm==0.9.0 --no-binary :all:` | accepts | no pin, reported | operator decision 2: a closed two-option table |
| `vllm==0.9.0 --has=sha256:aa` | accepts (optparse abbreviation) | no pin, reported | operator decision 2 |

## D7. Measured acceptance rows

With the prototype, against pip 26.1.2:

- The three rejected option rows: reported, no pin. `-Cfoo"="bar`: pin `{'vllm': '0.9.0'}`.
- `example.zip [foo] ==`, `example.zip [foo] (>=)`, `example.zip ==`, `example.zip >=`:
  reported. `example.zip [foo] ==1.0`: not reported.
- #528, 12 rows — attached `example.zip<X>==1.0`, detached `example.zip <X> ==1.0`, named URL
  `numpy <X> @ https://host/numpy.whl`, each for `[foo bar]`, `[foo,]`, `[,foo]`, `[-foo]` —
  all reported.
- 18 accept controls — the same three spellings for `[foo]`, `[foo,bar]`, `[ foo , bar ]`,
  `[]`, `[ ]`, `[foo.bar_1]` — none reported.
- The four non-ASCII rows `example.zip [İ] ==1.0`, `[ı]`, `[ſ]`, `[K]` (U+0130, U+0131,
  U+017F, U+212A) — all reported. pip rejects all four; there is no Kelvin accept case.
- The five monitored files: `_opaque_requirements` `[]`, pin counts 1, 5, 105, 106, 110,
  `_unpinned_protected` `{}`, `_conditional_protected` `{}`.

Rows added after the Loop-1 refuter pass (2026-10-08), measured with the corrected prototype:

| Line | pip 26.1.2 | Guard after commit B |
|---|---|---|
| `vllm==0.9.0; os_name == "a -Cbad"` | rejects (`Could not split options`) | no pin, reported, vllm unpinned |
| `vllm==0.9.0 ; os_name == "x --bogus"` | rejects | no pin, reported, vllm unpinned |
| `vllm==0.9.0 ;` | accepts, no marker | pin `0.9.0`, not reported, no conditional entry |
| `sympy==1.13.1;` | accepts, no marker | pin `1.13.1`, not reported |
| `vllm==0.9.0 ; bogus marker` | rejects | no pin, reported |

Measured at `5564e016`: the two marker-dash rows and `vllm==0.9.0 ; bogus marker` record the
pin `{'vllm': '0.9.0'}` and are not reported — `_requirement_lines` cuts at `;` before anything
reads the marker. They are **reds that commit B turns green** (commit A does not: its readers
still cut at `;` before `_PIN_PATTERN`). The two empty-marker rows already read as pins at
`5564e016`; they are **regression controls** that must stay green.

## D8. Measurement in the loop

`pip._internal` must never be imported by the module. A test may not import it either. To
measure a verdict, run a throwaway script (the issue body's "measurement only" command) and
write the result into the test's docstring.
