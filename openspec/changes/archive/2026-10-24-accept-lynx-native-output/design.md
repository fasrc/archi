## Context

`scripts/benchmarking/lynx/parse.py` turns one Lynx output into a verdict. Design D4 of
`openspec/changes/archive/2026-10-23-add-lynx-faithfulness-scorer/` made it strict: it
decodes every top-level JSON object with `json.JSONDecoder.raw_decode` at each `{` outside
an object it already decoded, and only the exact strings `PASS` and `FAIL`, after
`strip()`, are verdicts. A top-level `{` that does not start a complete object is a parse
error, so a cut-off answer such as `{"REASONING": {"SCORE": "PASS"}, "SCORE": "FAIL"`
cannot give the verdict of its nested object.

Real Lynx output is `{"REASONING": ['...', '...'], "SCORE": PASS}` (see the proposal for
the four outputs of 2026-10-24 and Patronus's own reader). `raw_decode` refuses it at the
first `'`, so no real row is ever scored.

**Goals:** read Lynx's own form; keep every refusal of D4; read valid JSON exactly as
before; stay standard-library only.

**Non-goals:** a general reader for Python literals; single-quoted keys; repairing output
that is neither JSON nor Lynx's form.

## Decisions

### D1. A small reader of JSON plus two forms, in place of `raw_decode`

`_top_level_objects` keeps its scan and its error message (`the '{' at offset N does not
start a complete JSON object: <reason>`). At each `{` it calls `_read_object(text, index)`
in place of `raw_decode`. The reader accepts this grammar (`ws` is JSON whitespace, `[ \t\n\r]*`):

```text
object   = "{" ws [ member *( ws "," ws member ) ] ws "}"
member   = jstring ws ":" ws value          ; when the key is "SCORE": value / word
array    = "[" ws [ value *( ws "," ws value ) ] ws "]"
value    = jstring / sqstring / number / object / array / "true" / "false" / "null"
jstring  = a JSON string, read by json.decoder.scanstring (strict)
number   = a JSON number; a float must be finite
sqstring = "'" ... "'"                     ; see D2
word     = 1*( %x41-5A / %x61-7A )         ; letters only; see D3
```

Valid JSON reads as `json` reads it: strings go through `json.decoder.scanstring`, numbers
match JSON's number pattern and become an `int` or a finite `float`, and keys must be JSON
strings. A test checks this against `json.JSONDecoder().raw_decode` over a set of JSON
objects, and checks that JSON's own refusals (`NaN`, `Infinity`, `1e309`, a trailing
comma, a missing colon, `01`, an unclosed object) still refuse.

Why not the alternatives:
- A regular expression like Patronus's (`"SCORE":\s*"?(\w+)"?`) does not know where an
  object ends, so it would read the nested verdict of a cut-off answer, which D4 refuses.
- `ast.literal_eval` cannot read the bare word, treats `true`, `false` and `null` as names,
  accepts Python forms JSON does not (tuples, sets, implicit string joins, `0x10`), and
  warns on stderr for an unknown escape. Rewriting the text into JSON first would need the
  same tokenizer, and it would shift the offsets in the error messages.

### D2. Single-quoted strings, with apostrophes kept

A single-quoted string is allowed wherever a value goes. Lynx writes English prose, so an
item can hold an apostrophe: `'The ANSWER's claim contradicts the CONTEXT.'`. A `'` inside
a single-quoted string ends it only when the next non-whitespace character is `,`, `]`,
`}` or the end of the text. Any other `'` is part of the string. A backslash keeps the
next character in the string, and the reader decodes `\\`, `\'`, `\"`, `\n`, `\t` and `\r`.
Any other escape stays as it is written. A raw control character (below U+0020) in the
string is an error, as it is in a JSON string. A string with no closing `'` is an error.

The rule still loses an item whose apostrophe is followed by one of those three
characters, such as `'the users', he said'`. The reader then ends the item early, meets the
bare word `he`, and the row is a parse error. That is a refusal, not a wrong verdict, and
the docs list it.

### D3. A bare word only as the value of `SCORE`

After the key `SCORE`, the reader also accepts a word of letters. `true`, `false` and
`null` stay JSON literals; any other word is the score as a string. So `"SCORE": PASS`
reads like `"SCORE": "PASS"`, and the verdict rule of D4 applies to both: `pass`, `Pass`
and `PAS` are parse errors that name the word, and `PASS.` breaks the object, because the
`.` is neither `,` nor `}`. A bare word anywhere else, such as `[maybe]` or `NaN`, is an
error, so the reader accepts no more than Lynx writes.

### D4. What stays a parse error

Each refusal of the 2026-10-23 D4 stays and keeps its test: no object, no `SCORE`, a
`SCORE` that is not exactly `PASS` or `FAIL`, two objects with different scores, `NaN`,
`Infinity` or a float overflow, a top-level `{` that does not start a complete object (a
cut-off answer or a stray `{` in prose). These cases of the new forms are added: a
cut-off Lynx object (no closing `}`, `"SCORE": PAS` at the end of the text), a bare word
that is not `PASS` or `FAIL`, a bare word outside `SCORE`, a single-quoted key, an
unclosed single-quoted string, and two Lynx objects with different scores.

### D5. The stub answers in Lynx's form

`backends.STUB_OUTPUT` becomes `{"REASONING": ['stub backend'], "SCORE": PASS}`. The stub
exists to test the file flow with no model, and the file flow includes the parser, so its
answer should look like Lynx's. The reasoning it records stays `["stub backend"]`.

## Risks / Trade-offs

- [The reader is new code where `json` was] → A test compares it with `json` over valid and
  invalid JSON objects, and every D4 refusal test stays unchanged.
- [Lynx 70B may write another form] → It is trained like the 8B model, so the same form is
  likely, but unverified. The first 70B run (#737) writes each parse error's raw output
  to the Lynx file, so a new form shows as `parse_error` rows with their text, never as a
  wrong verdict.
- [The apostrophe rule can still end an item early] → Only when an apostrophe is followed
  by `,`, `]` or `}`; the row is then a parse error, which is counted and shown.
