# HarvardKey + Archi RBAC — decisions, and the open question that blocks them

**Status: ON HOLD as of 2026-09-01.** No spec written. No code written.
The hold exists because question 1 in
[Questions for Harvard IAM](#questions-for-harvard-iam) can invalidate the protocol choice.

**What this document asks of you:** confirm question 1 with Harvard IAM, or tell us if a
decision below is wrong. Everything else is ready to turn into a spec.

## Decisions taken (2026-09-01)

1. **Scope of protection:** every user signs in with a HarvardKey to use chat. Named roles
   gate the privileged surface — upload, config, evaluations, alerts, and admin.
2. **Role source:** Harvard central groups (Grouper), not a list that Archi keeps.
3. **Protocol:** OpenID Connect, and Archi reads the `memberOf` claim.
4. **Group-to-role map:** an explicit map in `config.yaml`, read by a new
   `src/utils/rbac/group_mapper.py`. No naming convention. No second store in Postgres.

Archi already owns the whole permission layer, so none of this builds one. `src/utils/rbac/`
holds a role registry with inheritance, 25 permissions in a `Permission` enum, route
decorators, and audit logs. `src/interfaces/chat_app/app.py:3353` already registers a
generic OpenID Connect client through authlib. The layer is dormant: the FASRC dev
deployment sets `auth.enabled: false`.

The one thing that does not fit is the role source. `src/utils/rbac/jwt_parser.py` reads
`resource_access.<app>.roles`, a Keycloak claim shape inherited from CERN SSO upstream.
**HarvardKey sends no such claim under either protocol.** So this work replaces one
function's input, not the login code.

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
   `harvardEduNetId` unavailable in OIDC. Is `eduPersonPrincipalName` the right key, and does
   it stay constant across a name change?
7. Is a `*.rc.fas.harvard.edu` host acceptable for the redirect URI? Can one registration
   carry separate development and production redirect URIs, or do we register two
   applications?

## Three existing defects this work must fix

Found 2026-09-01 against `origin/dev`. All three are latent today, because
`auth.enabled: false` on the dev deployment.

1. **No HTTPS, and no proxy trust.** The chat app runs the bare Flask server
   (`src/interfaces/chat_app/app.py:3838`). Harvard registers one exact HTTPS redirect URI.
   Behind a TLS proxy, `url_for("sso_callback", _external=True)` emits `http://` unless the
   app trusts `X-Forwarded-Proto`. Nothing in the repository does.
   `docs/docs/advanced_setup_deploy.md:151` documents an nginx example that nothing deploys.
2. **A redirect loop for a refused user.** `sso_callback` handles an error with
   `redirect(url_for("login"))`, and `login` restarts SSO. Harvard's own page warns that OIDC
   returns unauthorized users to the application, which must handle the error itself. The
   result today is an endless loop rather than an explanation.
3. **Two role sources disagree.** The browser path reads roles from the token. The API path
   derives them from one boolean — `["admin"] if user.is_admin else [default_role]` at
   `src/interfaces/chat_app/openai_compat.py:159-161`. The same person gets `admin` in a
   browser and `base-user` through an API token.

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

`harvardEduNetId` and `displayName` also do not cross OIDC. Under OIDC the stable key becomes
`eduPersonPrincipalName`, and the display name falls back to `mail`.

## Prerequisites that no code change removes

1. **HKAR registration.** Submit the Application Integration Form with a benefits-eligible
   Harvard sponsor, the OIDC redirect URI, and an attribute request for the `memberOf`
   release bundle. Annual attestation follows.
2. **At least one authorization group.** Mandatory. Pick IAM reference groups, or ask IAM to
   build a custom group.
3. **One Grouper group for each privileged Archi role, provisioned to HLDAP or UNIVAD.** A
   group that exists in Grouper but is not provisioned there releases nothing.
4. **TLS on a registered hostname** in front of the deployment. Infrastructure, not code.
   This is the other half of defect 1.
5. **The answer to question 1.** This one can invalidate the protocol choice, so it comes
   before implementation, not during.

## Next step

Send the seven questions. Then write the spec against the confirmed protocol.

Milestone note: issue [#81](https://github.com/fasrc/archi/issues/81) (production readiness)
sits in `v2026.11.0`, and this work reads as a gate for that release.
