## ADDED Requirements

### Requirement: An operator can declare a context window per model id

The runtime SHALL let an operator declare a context window for an individual model id, through the same configuration block as the other in-loop settings, and SHALL use that declaration as the window for any run whose effective model is that id — whether the model was configured as the deployment's default or selected by a request-local override.

This closes the gap left by the two rules that precede it. A model named in the deployment's own configuration reports no window from its provider, because the value the provider would report is a library default rather than a property of the server. A window declared for the deployment as a whole is withdrawn from a request-local override, because it describes the model the deployment serves rather than the model the request selected. Both rules are correct, and together they leave a request-local override on a self-hosted model with no bound at all — the deployments most likely to overflow protect nothing, and the only trace is one warning line.

A per-model declaration is not subject to the second rule. The rule exists to require that the window and the model describe the same thing; an entry keyed by the overriding model's own id is that testimony, not a violation of it. The runtime therefore SHALL apply a per-model declaration to a request-local override, and a request-local run MUST NOT have a per-model declaration withdrawn from it.

Where the effective model has a per-model declaration, that declaration SHALL take precedence over a window declared for the deployment as a whole — for that model only, and whether or not the run is request-local. The two declarations differ in specificity rather than in trustworthiness, and an operator who wrote both meant the more specific one. Every other model keeps the deployment-wide window unchanged, so correcting one model MUST NOT require deleting the declaration the others depend on.

Where the effective model has no per-model declaration, behaviour SHALL be exactly what it is without this capability: a run that is not request-local uses the deployment-wide declared window, and a request-local run resolves the window from the provider serving it and — where that yields nothing — installs no bound and records at warning level that none was installed.

The declarations SHALL be validated with the same posture as every other setting in the block: an invalid declaration is logged and ignored, never treated as a disabled bound and never allowed to remove protection the other settings configure. Validation MUST be per entry, so one malformed declaration costs the operator that model's bound and no other. A declaration that is not a positive integer MUST be rejected, including a boolean, since a boolean is an integer in this language and a one-token window would clear every message on every call.

The model id SHALL be supplied to the precedence decision directly. It MUST NOT be recovered by parsing the combined provider-and-model label used for logging, because a model id may itself contain the separator that label is built with, and parsing it would resolve the wrong id on exactly the self-hosted deployments this requirement exists to protect.

A deployment that declares no per-model windows SHALL behave exactly as it does without this capability.

#### Scenario: A request-local override listed in the map gets a bound

- **WHEN** a request overrides the model with an id the packaged model list does not contain
- **AND** the configuration declares a context window for that exact model id
- **THEN** the in-loop bound for that request is derived from the declared entry
- **AND** the bound is installed even though the run is request-local

#### Scenario: An override absent from the map keeps today's fail-open

- **WHEN** a request overrides the model with an id that has no per-model declaration
- **AND** neither the provider serving the request nor the by-name lookup yields a window
- **THEN** no in-loop bound is installed
- **AND** the runtime logs the warning naming the provider and model
- **AND** no window belonging to another model is substituted

#### Scenario: A per-model entry outranks the deployment-wide window on the default model

- **WHEN** the configuration declares both a deployment-wide context window and a per-model window for the model the deployment is configured to serve
- **AND** the run is not request-local
- **THEN** the budget is derived from the per-model entry

#### Scenario: A per-model entry outranks the deployment-wide window on an override

- **WHEN** the configuration declares both a deployment-wide context window and a per-model window for an overriding model
- **THEN** the budget is derived from the per-model entry for that model
- **AND** the deployment-wide window is not applied to it

#### Scenario: A model absent from the map still uses the deployment-wide window

- **WHEN** the configuration declares a deployment-wide context window and a per-model map that does not name the effective model
- **AND** the run is not request-local
- **THEN** the budget is derived from the deployment-wide window, exactly as without this capability

#### Scenario: An invalid entry costs only its own model

- **WHEN** the per-model map holds one entry that is not a positive integer and one that is
- **THEN** the runtime logs a warning naming the rejected key
- **AND** the valid entry still declares its model's window
- **AND** no other configured protection is disabled

#### Scenario: A boolean value is rejected

- **WHEN** a per-model entry declares a boolean as the window
- **THEN** the entry is logged and ignored
- **AND** no bound is sized from it

#### Scenario: A map that is not a mapping is ignored

- **WHEN** the per-model setting is present but is not a mapping
- **THEN** the runtime logs a warning and treats it as absent
- **AND** a deployment-wide declared window still applies

#### Scenario: An absent or empty map changes nothing

- **WHEN** the configuration declares no per-model windows, or declares an empty map
- **THEN** every run behaves exactly as it does without this capability

#### Scenario: A model id containing the label separator resolves correctly

- **WHEN** the effective model id itself contains the separator used to build the provider-and-model log label
- **AND** the configuration declares a per-model window under that full id
- **THEN** the declaration is matched and the bound is derived from it
