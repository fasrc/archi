# HarvardKey + Archi RBAC — decisions, and the open question that blocks them

**Status: ON HOLD as of 2026-09-01.** No spec written. No code written.
The hold exists because question 1 in
[Questions for Harvard IAM](#questions-for-harvard-iam) can invalidate the protocol choice.

**What this document asks of you:** confirm question 1 with Harvard IAM, or tell us if a
decision below is wrong. Everything else is ready to turn into a spec.

## Decisions taken (2026-09-01)

1. **Scope of protection:** every user signs in with a HarvardKey to use chat. Named roles
   gate the privileged surface — upload, config, evaluations, alerts, and admin.
2. **Role source:** Harvard central groups (Grouper), not a list that Archi keeps. That
   retires the two lists Archi keeps today: `services.chat_app.alerts.managers` (defect 7)
   and `users.is_admin` as a role source (defect 3).
3. **Protocol:** OpenID Connect, and Archi reads the `memberOf` claim.
4. **Group-to-role map:** an explicit map in `config.yaml`, read by a new
   `src/utils/rbac/group_mapper.py`. No naming convention. No second role store in
   Postgres, so an API bearer token carries the default role only (defect 3).

Archi already owns the whole permission layer, so none of this builds one. `src/utils/rbac/`
holds a role registry with inheritance, 26 permissions in a `Permission` enum, route
decorators, and audit logs. `src/interfaces/chat_app/app.py:3353` already registers a
generic OpenID Connect client through authlib. The layer is dormant: the FASRC dev
deployment sets `auth.enabled: false`.

The one thing that does not fit is the role source. `src/utils/rbac/jwt_parser.py` reads
`resource_access.<app>.roles`, a Keycloak claim shape inherited from CERN SSO upstream.
**HarvardKey sends no such claim under either protocol.** So this work replaces the
role-extraction function, plus one small change in `sso_callback`. Today the callback fetches
UserInfo into a local variable only when the ID token carries no claims (`app.py:3467-3470`),
then calls `get_user_roles(token, …)` with the token alone (`:3478`). If IAM answers question 2
with "UserInfo only", the mapper never sees `memberOf` unless the callback fetches UserInfo
whenever the ID token lacks the claim and hands the result to the mapper explicitly.

## Why the protocol choice needs confirmation

Two Harvard pages disagree.

- The **HarvardKey Attributes Available Table** (sheet id
  `1Fbv8HldQ0a9VGwEyMZevyAuqNtpv0qCJ0-iwzCOXC0c`, gid `848693615`) lists `memberOf`
  (`urn:oid:1.3.6.1.4.1.19376.1.2.4.1.6`) as available in SAML **and in OIDC**, at all risk
  levels, with no riders required.
- **[Selecting a HarvardKey Authentication Protocol](https://www.iam.harvard.edu/selecting-harvardkey-authentication-protocol)**
  says OIDC supports only first name, last name, display name, nickname, email, profile URL,
  and preferred username, and it directs applications that need group membership to SAML.

If the table is right, Archi reuses the OIDC client it already has. If the article is right,
the work grows by a Shibboleth service provider: a new container, an SP keypair with
rotation, `xmlsec1` in the image, and metadata exchange with IAM.

## Questions for Harvard IAM

Send to `iam_help@harvard.edu`.

1. Can a HarvardKey OIDC application receive the `memberOf` attribute? The Attributes
   Available Table says yes. The protocol-selection article implies no. Which applies to a
   new integration?
2. If OIDC can receive it: which scope must the client request, and does the claim arrive in
   the ID token, from the UserInfo endpoint, or both?
3. What is the value format? The table says "Grouper filter string". Is that a full path such
   as `harvard:org:fasrc:archi-admins`, or a flat name? Does it arrive as a JSON array?
4. The table populates `memberOf` only "for groups provisioned to HLDAP or UNIVAD". What is
   the process to provision a new custom Grouper group to one of those, and how long does it
   take?
5. Can one application use several custom groups — one for each privilege tier, for example
   `archi-admins` and `archi-operators` — while the application's own authorization group
   stays broad enough that a non-privileged person still signs in?
6. Over OIDC, which claim is the recommended stable unique identifier? The table marks
   `harvardEduNetId` unavailable in OIDC. Archi keys `users.id` on the OIDC `sub` claim today
   (`src/interfaces/chat_app/app.py:3482`). Is `sub` stable and unique for one person across
   sessions and a name change? If not, is `eduPersonPrincipalName` the right key, and does it
   stay constant across a name change?
7. Is a `*.rc.fas.harvard.edu` host acceptable for the redirect URI? Can one registration
   carry separate development and production redirect URIs, or do we register two
   applications?

## Existing defects this work must fix

Items 1 to 3 were found 2026-09-01 against `origin/dev`. Items 4 to 7 came out of the review
of PR #398 the same day, verified against the same tree. All seven are latent today, because
`auth.enabled: false` on the dev deployment.

1. **No HTTPS, no proxy trust, and an insecure session cookie.** The chat app runs the bare
   Flask server (`src/interfaces/chat_app/app.py:3838`). Harvard registers one exact HTTPS
   redirect URI. Behind a TLS proxy, `url_for("sso_callback", _external=True)` emits
   `http://` unless the app trusts `X-Forwarded-Proto`. Nothing in the repository does.
   `docs/docs/advanced_setup_deploy.md:151` documents an nginx example that nothing deploys.
   The same setup leaves `SESSION_COOKIE_SECURE` unset (`app.py:2797-2801`), so once TLS
   terminates upstream, the HarvardKey-backed session cookie still travels in plaintext to
   the published port. The code half of the fix is proxy trust plus that flag; the
   infrastructure half is prerequisite 4.
2. **No explanation for a refused user.** `sso_callback` catches the provider error, calls
   `flash("Authentication failed: …")`, and redirects to `/login` (`app.py:3536-3537`).
   `login` starts SSO only when the request carries `?method=sso` (`:3398`), so there is no
   redirect loop. But it renders `landing.html` (`:3426`), and that template has no
   flashed-messages block — only `login.html` has one, and the chat app never renders it.
   The user lands on the plain login page with no reason. Harvard's own page warns that OIDC
   returns unauthorized users to the application, which must handle the error itself.
3. **Two role sources disagree.** The browser path reads roles from the token. The API path
   derives them from one boolean — `["admin"] if user.is_admin else [default_role]` at
   `src/interfaces/chat_app/openai_compat.py:159-161`. The same person gets `admin` in a
   browser and `base-user` through an API token. A bearer request carries no OIDC token, no
   UserInfo response, and no `memberOf` (`openai_compat.py:142-161`), so the group mapper
   cannot serve it, and decision 4 rules out a role store in Postgres. Resolution: bearer
   requests take the default role only, and the `is_admin` shortcut goes. `/v1` checks only
   `chat:query` (`openai_compat.py:106`, `:166`), so no API caller loses a permission it
   uses today. The alternative — caching the last resolved roles on the `users` row at each
   browser login — is a second role store with a revocation lag, and decision 4 rejects it.
4. **The role registry fails open.** With no `auth_roles` block, `load_rbac_config()`
   returns a `base-user` role with `permissions: ["*"]` (`src/utils/rbac/registry.py:465-479`),
   and the rendered config carries no `auth_roles` unless the deployment supplies one
   (`src/cli/templates/base-config.yaml:148-150`). Turn HarvardKey on in that state and every
   signed-in user holds every permission. With `auth.enabled: true`, a missing or empty
   registry must refuse to start, and the deployment must ship a complete `auth_roles` block
   that names every role in the group-to-role map.
5. **Agent-spec writes are gated by login alone.** `/api/agents` POST and DELETE and
   `/api/agents/active` POST are registered with `require_auth` only (`app.py:3088-3104`),
   while every other privileged write goes through `require_perm`: `update_config` takes
   `config:modify` (`:2935`), uploads take `upload:*`, the database viewer takes
   `database:admin`. The three agent handlers (`:4181`, `:4221`, `:4340`) write shared agent
   specs to disk and set the active agent in Postgres. Once every HarvardKey holder can sign
   in, any of them can change the system prompt and tool set for everyone. These routes
   must take `config:modify`.
6. **The data-manager service sits outside the chat app's auth and fails open.** It is a
   separate Flask app with its own basic auth. `services.data_manager.auth.enabled` defaults
   to `false` (`src/cli/templates/base-config.yaml:161-163`), `require_admin` passes every
   request through in that state (`src/interfaces/uploader_app/app.py:191-203`), and the
   compose template publishes its port on the host (`src/cli/templates/base-compose.yaml:81-82`).
   HarvardKey on the chat app changes none of that: a caller who can reach the port can
   upload and ingest with no role at all. This document keeps the data-manager outside
   HarvardKey; production turns its own auth on and keeps the port off any reachable
   interface (prerequisite 6).
7. **A username allowlist bypasses `alerts:manage`.** `is_alert_manager()` grants alert
   management when the session username appears in `services.chat_app.alerts.managers`
   (`src/interfaces/chat_app/service_alerts.py:44-61`), whatever the roles say. Removing a
   person from the Grouper group would not revoke it while the name stays in the config.
   Decision 2 retires the list: alert management flows from `alerts:manage` alone, and the
   config key goes.

## Harvard mechanics worth keeping

Harvard splits access control in two, and only one half depends on the protocol.

- **Authorization groups** sit on the identity-provider side. They are mandatory: every
  HarvardKey application names at least one, and Harvard tests membership before it returns
  the user. The stated reason is that a HarvardKey keeps working after a person leaves
  Harvard. This half needs no Archi code.
- **Attribute release** is optional and privacy-reviewed. It supplies the data that drives
  Archi roles.

Affiliation attributes (`eduPersonAffiliation`, `harvardEduPersonAffiliation`,
`eduPersonScopedAffiliation`) are SAML-only, and most need GDPR and personal-data riders at
risk level 3 to 4. Group membership is not in that category. That distinction is what makes
OIDC viable here: Archi's permissions are group-shaped, not affiliation-shaped.
`config:modify` does not follow from "staff" or "FAS". It follows from "this named person
operates Archi", which is a Grouper group.

`harvardEduNetId` and `displayName` also do not cross OIDC. Archi keys users on the OIDC `sub`
claim today and keeps doing so until IAM answers question 6; whether `sub` or
`eduPersonPrincipalName` is the stable key is that question, not a decision here. The display
name falls back to `mail`.

## Prerequisites that no code change removes

1. **HKAR registration.** Submit the Application Integration Form with a benefits-eligible
   Harvard sponsor, the OIDC redirect URI, and an attribute request for the `memberOf`
   release bundle. Annual attestation follows.
2. **At least one authorization group.** Mandatory. Pick IAM reference groups, or ask IAM to
   build a custom group.
3. **One Grouper group for each privileged Archi role, provisioned to HLDAP or UNIVAD.** A
   group that exists in Grouper but is not provisioned there releases nothing.
4. **TLS on a registered hostname** in front of the deployment. This is the infrastructure
   half of defect 1; the code half (proxy trust and `SESSION_COOKIE_SECURE`) stays in the
   defect list.
5. **The answer to question 1.** This one can invalidate the protocol choice, so it comes
   before implementation, not during.
6. **Data-manager auth on, and its port off the public interface.** Set
   `services.data_manager.auth.enabled: true`, and keep `data_manager_port_host` off any
   interface a browser can reach (defect 6).

## Next step

Send the seven questions. Then write the spec against the confirmed protocol.

Milestone note: issue [#81](https://github.com/fasrc/archi/issues/81) (production readiness)
sits in `v2026.11.0`, and this work reads as a gate for that release.
