## ADDED Requirements

### Requirement: The base-image guard SHALL read each requirement line in pip's order, with packaging for the requirement and a closed table for the options
The base-image dependency guard SHALL split each logical requirement line the way pip's `break_args_options` does, SHALL accept the option half only when every token that starts with `-` is a `--hash` or `-C`/`--config-settings` spelling that pip accepts, and SHALL read the requirement half with `packaging.requirements.Requirement` after pip's `${VAR}`, URL, path and archive checks.
Every guard in the module skips when its subject is absent, so a misread line is a silent skip. The regex reader recorded an exact pin from lines pip refuses (`vllm==0.9.0 -Cfoo=bar -Cbad`, `vllm==0.9.0 --hash=sha256:aa --bogus`) and missed malformed extras and bare operators that pip refuses (`example.zip[foo bar]==1.0`, `example.zip ==`). Measured at `5564e016` against pip 26.1.2 and packaging 26.2; operator decision recorded on issue #530 on 2026-09-26. The option table is stricter than pip on purpose: `--no-binary :all:` on a requirement line and an optparse abbreviation such as `--has=` are reported. pip's own internals are never imported by the module.

#### Scenario: An option pip refuses keeps the pin from being read
- **WHEN** a requirement file read by the guard contains `vllm==0.9.0 -Cfoo=bar -Cbad`, `vllm==0.9.0 --hash=sha256:aa --bogus`, or `vllm==0.9.0 --hash=sha256:aa -Cbad`
- **THEN** the pin reader records no pin for vllm
- **AND** the line is reported as a requirement the module cannot read
- **AND** vllm stays visible to the unpinned-protected check, so the suite fails rather than skips

#### Scenario: A dash inside a quoted marker value keeps the pin from being read
- **WHEN** a requirement file read by the guard contains `vllm==0.9.0; os_name == "a -Cbad"` or `vllm==0.9.0 ; os_name == "x --bogus"`
- **THEN** the pin reader records no pin for vllm, as pip refuses each line
- **AND** the line is reported
- **AND** the guard does not rely on the requirement parser to fail on such a line

#### Scenario: The marker is split off at the first semicolon, as pip splits it
- **WHEN** a requirement file read by the guard contains `vllm==0.9.0 ;` or `sympy==1.13.1;`
- **THEN** the pin reader records the exact pin, as pip reads an empty marker as no marker
- **AND** nothing is reported and no conditional pin is recorded
- **AND** `vllm==0.9.0 ; bogus marker` records no pin and is reported

#### Scenario: A quoted option value is read as shlex reads it
- **WHEN** a requirement file read by the guard contains `vllm==0.9.0 -Cfoo"="bar`
- **THEN** the pin reader records the exact pin `0.9.0`
- **AND** nothing is reported

#### Scenario: A hash option is checked against pip's allowed algorithms
- **WHEN** a requirement line carries `--hash=sha256:aa` or `--hash sha256:aa`
- **THEN** the option is cut and the exact pin is read
- **AND** a line that carries `--hash=md5:aa` or `--hash=sha256` records no pin

#### Scenario: A bare comparison operator is reported
- **WHEN** a requirement file read by the guard contains `example.zip [foo] ==`, `example.zip [foo] (>=)`, `example.zip ==`, or `example.zip >=`
- **THEN** each line is reported
- **AND** `example.zip [foo] ==1.0` is not reported

#### Scenario: A malformed extras list is reported in every spelling
- **WHEN** a requirement line carries `[foo bar]`, `[foo,]`, `[,foo]`, or `[-foo]` attached to `example.zip`, detached from `example.zip` before `==1.0`, or in front of `@ https://host/numpy.whl`
- **THEN** each of the 12 lines is reported
- **AND** the same three spellings with `[foo]`, `[foo,bar]`, `[ foo , bar ]`, `[]`, `[ ]`, or `[foo.bar_1]` are not reported

#### Scenario: A non-ASCII extra name is reported
- **WHEN** a requirement line is `example.zip [X] ==1.0` where X is U+0130, U+0131, U+017F, or U+212A
- **THEN** each line is reported, as pip refuses each one

#### Scenario: The pin is a single exact clause
- **WHEN** a requirement line is `vllm == 0.9.0`, `vllm (==0.9.0)`, or `vllm===0.9.0`
- **THEN** the pin reader records the pin `0.9.0`
- **AND** `vllm==1.0.*` and `vllm==0.9.0,<0.10` record no pin and vllm reads as unpinned

#### Scenario: The four stricter-than-pip rows stay reported
- **WHEN** a requirement line is `vllm-0.9.0-py3-none-any.whl[foo]`, `evil@../pkgs/vllm`, `vllm==0.9.0 --no-binary :all:`, or `vllm==0.9.0 --has=sha256:aa`
- **THEN** the line is reported although pip accepts it
- **AND** a test pins each row with a comment that says it is stricter than pip

#### Scenario: The monitored files read the same as before
- **WHEN** the guard reads the CPU header, the GPU header, `requirements-base.txt`, and the two generated base-image requirements files
- **THEN** no line is reported
- **AND** the pin counts are 1, 5, 105, 106, and 110
