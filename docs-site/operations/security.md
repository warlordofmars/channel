# Security and secrets

This page documents the operational secret contracts that Channel
relies on at runtime: which environment variables and SSM parameters
they map to, which file in the codebase consumes them, how to rotate
them safely, and what the startup-time fail-closed posture looks like.

All SSM parameters use a per-environment path so non-prod and prod never
share secrets:

```text
prod   → /channel/<name>
others → /channel/<env_name>/<name>
```

The CDK stack in `infra/stacks/channel_stack.py` provisions most
secret parameters with a `CHANGE_ME_ON_FIRST_DEPLOY` placeholder and
applies `RemovalPolicy.RETAIN` so a stack delete never destroys the
live secret material. The `AllowedEmails` parameter is the exception:
it ships with `"[]"` (deny-all) by default so a freshly-deployed
stack never grants management access to anyone until the deployer
explicitly populates the list.

> **Fail-closed startup validation is tracked under
> [issue #16](https://github.com/warlordofmars/agentcore-starter/issues/16).**
> Today, several of the contracts below tolerate the
> `CHANGE_ME_ON_FIRST_DEPLOY` placeholder by silently degrading
> behaviour (origin-verify is the clearest example). Once #16 lands,
> the application will refuse to start when any required secret is
> still unrotated. Sections below call out both the **current**
> behaviour and the **post-#16** behaviour where they differ.

## Origin verification (CloudFront → Lambda)

In prod, CloudFront injects a shared secret on every request to the
Lambda Function URL via the `X-Origin-Verify` header. When that
header injection is enabled, the Lambda middleware rejects requests
that do not present the expected value, so the public Function URL
cannot be hit directly to bypass CloudFront's WAF,
geo-restrictions, and CSP headers.

| Field | Value |
| --- | --- |
| Env var (override) | `STARTER_ORIGIN_VERIFY_SECRET` |
| Env var (SSM path) | `STARTER_ORIGIN_VERIFY_PARAM` |
| SSM parameter path | `/channel/origin-verify-secret` (prod) or `/channel/<env_name>/origin-verify-secret` |
| Header name | `X-Origin-Verify` |
| Consumer (Lambda) | `src/channel/auth/tokens.py` (`_origin_verify_secret()`) |
| Consumer (middleware) | `src/channel/api/main.py` (`_verify_origin_secret`) |
| Injector (CloudFront) | `infra/stacks/channel_stack.py` (`origin_verify_header`, all environments) |

### Current behaviour

The middleware actively verifies the header when
`STARTER_ORIGIN_VERIFY_PARAM` (or `STARTER_ORIGIN_VERIFY_SECRET`) is
set in the Lambda environment and the resolved SSM value can be read.
The request is rejected if the incoming `x-origin-verify` header does
**not** match the resolved value; a matching header is the success
case and the request is allowed through.

The CDK stack sets `STARTER_ORIGIN_VERIFY_PARAM` and injects
`X-Origin-Verify` from CloudFront in **every** environment. The fail-
closed startup validator (issue #16) refuses to start the Lambda when
the SSM parameter still holds the `CHANGE_ME_ON_FIRST_DEPLOY`
placeholder, so a half-configured deploy never silently degrades —
Lambda init fails, `/health` returns the runtime error body, and the
CI smoke test for the dev environment trips the pipeline red. **Rotate
the secret immediately after the first deploy in any environment**
using the procedure below.

The resolver in `src/channel/auth/tokens.py` (`_origin_verify_secret`)
returns `None` on any SSM exception (missing parameter, IAM denial,
network error). When `None` is returned, the middleware skips
verification — but the startup validator already proved the parameter
is readable at cold start, so this branch only fires on a transient
SSM outage *after* a successful warm-up read. Because
`_origin_verify_secret()` is wrapped in `functools.lru_cache(maxsize=1)`,
the first successful read pins the value for the warm container's
lifetime; the failure branch only matters if the very first read is a
transient failure and gets cached as `None`. The startup validator
makes that window narrow but not zero — see #16's discussion of a
follow-up to resolve the value at startup and pass it into the
middleware.

### Rotation procedure

> **Keep `Type=String`. Do not rotate to `SecureString`.**
> The CloudFront origin custom-header injection uses
> `CfnDynamicReferenceService.SSM`, which only resolves plaintext SSM
> parameters. Rotating to `SecureString` causes the dynamic reference
> to resolve to `null`, CloudFront stops sending `X-Origin-Verify`,
> and every request through CloudFront returns 403 from the Lambda
> middleware. The secret is defense-in-depth — preventing direct
> Function URL access from outside the AWS account — not crypto. The
> IAM-gated visibility on SSM and CloudFront origin config is the
> security boundary, and `String` is sufficient.

1. Generate a new high-entropy value (e.g. `openssl rand -hex 32`).
2. Update the SSM parameter (canonical command form — note
   `--type String` and `--overwrite`):
   ```bash
   aws ssm put-parameter \
     --name /channel/<env>/origin-verify-secret \
     --value "$NEW_SECRET" --type String --overwrite
   ```
   For prod, the parameter path drops the `<env>/` segment:
   `/channel/origin-verify-secret`.
3. Re-deploy the stack. **By itself this is not enough** — see the
   WARNING immediately below. CDK synthesizes the
   `CfnDynamicReference` in `origin_verify_header` into the
   CloudFormation template, and CloudFormation resolves the resulting
   <code v-pre>{{resolve:ssm:...}}</code> reference during stack
   create/update. Because the template string stays byte-identical
   across SSM rotations, a bare `aws ssm put-parameter` won't
   propagate the new value, and the redeploy alone won't either. The
   WARNING below explains why and gives the manual workaround required
   after every rotation until [#116](https://github.com/warlordofmars/agentcore-starter/issues/116)
   lands.

   > **WARNING — `cdk deploy` does NOT propagate the rotated SSM
   > value to CloudFront.** The CloudFront origin custom-header for
   > `X-Origin-Verify` is wired with `CfnDynamicReferenceService.SSM`,
   > which renders into the CloudFormation template as a fixed
   > literal string of the form <code v-pre>{{resolve:ssm:/channel/&lt;env&gt;/origin-verify-secret}}</code>.
   > That template string is identical across SSM rotations, so
   > CloudFormation sees no diff on the `UiDistribution` resource
   > and CloudFront's stored distribution config is never updated.
   > Tracked as [#116](https://github.com/warlordofmars/agentcore-starter/issues/116);
   > until the architectural fix lands, follow the manual workaround
   > below after every origin-verify rotation.
   >
   > **Failure mode.** The next Lambda cold start reads the new
   > secret from SSM directly, but CloudFront keeps sending the old
   > secret on every origin request. Result: every CloudFront-routed
   > request returns 403 from `_verify_origin_secret`, surfacing
   > minutes to hours after the deploy depending on cold-start
   > timing. The deploy logs show success and there is no signal at
   > the moment of action that anything is wrong — failure mimics
   > success.
   >
   > **CLI workaround.** Splice the new secret into CloudFront's
   > distribution config directly. Validated against the dev and jc
   > distributions on 2026-04-29. The intermediate files contain the
   > rotated secret in plaintext, so the snippet uses `mktemp` and
   > a `trap` to wipe them on exit:
   >
   > ```bash
   > # 0. Re-export the value just written to SSM in step 2 of the
   > #    outer rotation procedure. The patch in step 3 below splices
   > #    NEW_SECRET into CloudFront's distribution config.
   > export NEW_SECRET="<the-same-value-passed-to-aws-ssm-put-parameter>"
   >
   > # Allocate temp files (with explicit templates for portability
   > # across GNU coreutils and BSD mktemp) and ensure they are
   > # removed on exit — they contain the rotated secret in plaintext.
   > DIST_CONFIG=$(mktemp "${TMPDIR:-/tmp}/dist-config.XXXXXX")
   > DIST_BODY=$(mktemp "${TMPDIR:-/tmp}/dist-body.XXXXXX")
   > DIST_BODY_PATCHED=$(mktemp "${TMPDIR:-/tmp}/dist-body-patched.XXXXXX")
   > trap 'rm -f "$DIST_CONFIG" "$DIST_BODY" "$DIST_BODY_PATCHED"' EXIT
   >
   > # 1. Resolve the distribution ID for this environment. The
   > #    CloudFront alternate domain name is the full FQDN — for dev /
   > #    jc / non-prod environments,
   > #    `channel-<env>.<hosted-zone>`; for prod,
   > #    `channel.<hosted-zone>` (no env suffix). The
   > #    JMESPath uses exact-match (`@ == ...`) on the full FQDN to
   > #    avoid matching unrelated distributions whose alias happens to
   > #    share the `channel-<env>` prefix (e.g. `dev` vs
   > #    `dev2`) — `contains(@, ...)` would silently return multiple
   > #    IDs and break the get-distribution-config call below.
   > # Non-prod (replace <env> and <hosted-zone>):
   > DIST_ID=$(aws cloudfront list-distributions \
   >   --query "DistributionList.Items[?Aliases.Items[?@ == 'channel-<env>.<hosted-zone>']].Id" \
   >   --output text)
   > # Prod equivalent (uncomment for prod, comment out the form above):
   > # DIST_ID=$(aws cloudfront list-distributions \
   > #   --query "DistributionList.Items[?Aliases.Items[?@ == 'channel.<hosted-zone>']].Id" \
   > #   --output text)
   >
   > # 2. Fetch the current distribution config + ETag. The response
   > #    has top-level keys DistributionConfig and ETag; update-distribution
   > #    accepts only the inner DistributionConfig as its --distribution-config
   > #    body, so split the two.
   > aws cloudfront get-distribution-config --id "$DIST_ID" > "$DIST_CONFIG"
   > ETAG=$(jq -r '.ETag' "$DIST_CONFIG")
   > jq '.DistributionConfig' "$DIST_CONFIG" > "$DIST_BODY"
   >
   > # 3. Patch the X-Origin-Verify HeaderValue under the Lambda
   > #    Function URL origin (the origin whose CustomHeaders.Quantity > 0).
   > jq --arg new "$NEW_SECRET" '
   >   .Origins.Items |= map(
   >     if (.CustomHeaders.Quantity // 0) > 0 then
   >       .CustomHeaders.Items |= map(
   >         if .HeaderName == "X-Origin-Verify" then .HeaderValue = $new else . end
   >       )
   >     else . end
   >   )
   > ' "$DIST_BODY" > "$DIST_BODY_PATCHED"
   >
   > # 4. Push the update with the captured ETag.
   > aws cloudfront update-distribution \
   >   --id "$DIST_ID" \
   >   --if-match "$ETAG" \
   >   --distribution-config "file://$DIST_BODY_PATCHED"
   > ```
   >
   > **Verification.** Run the verification curls **only after
   > completing rotation step 4 below** (the outer rotation
   > procedure's Lambda bounce, which flushes the
   > `_origin_verify_secret` lru-cache — distinct from the
   > workaround snippet's own `# 4. Push the update…` step).
   > Between `update-distribution` finishing and the Lambda bounce,
   > CloudFront is sending the new header value while Lambda is
   > still validating against the old cached value — every
   > CloudFront-routed request 403s in that window, and the 403 is
   > *expected*, not a failed CloudFront update. Wait for the
   > distribution status to reach `Deployed` (a few minutes),
   > perform rotation step 4, then run:
   >
   > ```bash
   > # Direct Function URL with no header → 403 (Lambda rejects).
   > curl -sS -o /dev/null -w "%{http_code}\n" \
   >   "https://<lambda-function-url-host>/health"
   > # Expect: 403
   >
   > # /health via CloudFront → 200 with status:ok ONLY after
   > # rotation step 4 has refreshed Lambda's cached SSM secret to
   > # match CloudFront's injected header value.
   > curl -sS "https://channel-<env>.<hosted-zone>/health"
   > # Expect after rotation step 4: {"status":"ok","version":"..."}
   > ```
   >
   > Both observations together confirm CloudFront is now sending
   > the rotated secret and Lambda accepts it. If only the second
   > step is verified, the validation is incomplete — a stale
   > CloudFront header that happens to match a stale Lambda cache
   > would also pass `/health`.

4. Bounce the Lambda (e.g. publish a new version or update an
   environment variable) so the `lru_cache` on `_origin_verify_secret`
   re-reads SSM. CloudFront and Lambda are briefly out of sync during
   this window — schedule rotations during low traffic.

#### If the parameter type ever needs to change

> **The same propagation gap from the WARNING in step 3 applies
> here.** Until [#116](https://github.com/warlordofmars/agentcore-starter/issues/116)
> lands, references to "after the CDK redeploy" in this section
> should be read as "after the CDK redeploy **and** the manual
> `aws cloudfront update-distribution` step from the WARNING above".
> The bullets describe what *would* happen if redeploy alone
> propagated the SSM value, and translate to the post-#116 steady
> state — they are still useful for understanding the misalignment
> windows, but the manual step has to land in the same operator
> sequence today.

`aws ssm put-parameter --overwrite` cannot change a parameter's type;
it can only update the value of an existing parameter of the same
type. Changing the type requires `aws ssm delete-parameter` followed
by a fresh `put-parameter` with the new type.

A delete-then-put sequence opens a misalignment window between
CloudFront and Lambda:

- After the `delete-parameter` call but before the next CDK deploy,
  CloudFront still has the **old** secret cached in its origin
  custom-header config; Lambda's `_origin_verify_secret` lru-cache
  also still has the old value (until the Lambda cold-starts). New
  requests pass.
- After `put-parameter` (with the new value) but before the CDK
  redeploy, the SSM parameter holds the new value but CloudFront is
  still injecting the old. Lambda will reject every CloudFront
  request as soon as its lru-cache expires (next cold start).
- After the CDK redeploy and the manual `update-distribution` step
  (per the WARNING above), but before the Lambda cold-starts,
  CloudFront sends the new header value while Lambda is still
  validating against the old. Every request 403s.

Recommend doing the type change during a maintenance pause: announce
downtime, run delete-then-put, redeploy CDK, run the manual
`aws cloudfront update-distribution` workaround from the WARNING in
step 3, then force a Lambda cold start (publish a new version or
touch an environment variable). Verify `/health` returns 200 through
CloudFront and 403 against the direct Function URL before lifting the
maintenance window.

## JWT signing secret

Every JWT issued by the application — today, the management session
tokens; once you add OAuth 2.1 token issuance, the bearer access
tokens too — is signed with HS256 using a single shared secret
stored in SSM. The template ships only the OAuth 2.1 discovery
documents (`/.well-known/oauth-authorization-server` and
`/.well-known/oauth-protected-resource`); the
`/oauth/authorize` and `/oauth/token` endpoints are not implemented
yet.

| Field | Value |
| --- | --- |
| Env var (override) | `STARTER_JWT_SECRET` |
| Env var (SSM path) | `STARTER_JWT_SECRET_PARAM` |
| SSM parameter path | `/channel/jwt-secret` (prod) or `/channel/<env_name>/jwt-secret` |
| Algorithm | HS256 (symmetric) |
| Issuer claim (`iss`) | `https://<custom_domain>` (set via `STARTER_ISSUER`) |
| Consumer | `src/channel/auth/tokens.py` (`_jwt_secret()`, `decode_jwt`, `decode_mgmt_jwt`) |

### Current behaviour

`_jwt_secret()` resolves the secret in this order:

1. `STARTER_JWT_SECRET` env var (used by tests and local dev).
2. SSM `STARTER_JWT_SECRET_PARAM` (Lambda runtime).
3. A random 32-byte fallback generated per process — intended as a
   **local-dev escape hatch**, but `_jwt_secret()` also takes this
   branch on **any** SSM exception (missing parameter, IAM denial,
   network error). In Lambda this means tokens issued by one cold
   start will not validate on the next, silently invalidating every
   client session.

The fallback is what makes a half-configured deploy dangerous: if
SSM is unreachable or the parameter is missing, every cold start
issues a new secret and previously-issued tokens silently stop
validating. Issue #16 will replace the fallback with a hard startup
error in deployed environments.

### Rotation procedure

Rotating the JWT secret invalidates **every** outstanding token —
both API access tokens and active management UI sessions. Plan
accordingly.

1. Generate a new value: `openssl rand -hex 32`.
2. Update SSM:
   ```bash
   aws ssm put-parameter \
     --name /channel/jwt-secret \
     --value "$NEW_SECRET" --type String --overwrite
   ```
3. Force a Lambda cold start so `lru_cache` re-reads (publish a new
   version or touch an env var).
4. All clients — including the management UI — must re-authenticate.

## Google OAuth

The management UI authenticates human users via Google OAuth 2.0.
Only emails on the allowlist are admitted, and the same allowlist
doubles as the admin-role grant list.

| Field | Value |
| --- | --- |
| Client ID env var | `GOOGLE_CLIENT_ID` |
| Client ID SSM env var | `GOOGLE_CLIENT_ID_PARAM` |
| Client ID SSM path | `/channel/google-client-id` (prod) or `/channel/<env_name>/google-client-id` |
| Client secret env var | `GOOGLE_CLIENT_SECRET` |
| Client secret SSM env var | `GOOGLE_CLIENT_SECRET_PARAM` |
| Client secret SSM path | `/channel/google-client-secret` (prod) or `/channel/<env_name>/google-client-secret` |
| Allowlist env var | `ALLOWED_EMAILS` (JSON array literal) |
| Allowlist SSM env var | `ALLOWED_EMAILS_PARAM` |
| Allowlist SSM path | `/channel/allowed-emails` (prod) or `/channel/<env_name>/allowed-emails` |
| Allowlist default | `"[]"` — empty list, denies all |
| Redirect URI | `https://<custom_domain>/auth/callback` |
| Consumer | `src/channel/auth/google.py`; login flow in `src/channel/auth/mgmt_auth.py` |

### Current behaviour

- The client ID and secret cache on first read
  (`functools.lru_cache`); changing them in SSM requires a Lambda
  bounce.
- The allowlist is wrapped in a 60-second TTL cache so operators can
  add or remove emails without forcing a cold start.
- `_allowed_emails()` **fails closed** on parse or load errors: a
  malformed JSON value or an unreachable SSM call is logged and
  treated as an empty list. No login is admitted.
- An empty allowlist (`"[]"` — the default after first deploy) means
  every login attempt is rejected with HTTP 403, including the
  deployer's own. Populate the parameter before expecting anyone to
  log in.
- Admin role is granted by the `is_admin_email` heuristic in
  `src/channel/auth/google.py`, which today maps to membership in
  the same allowlist — every allowlisted email is an admin. Replace
  this if you need a more granular role split.

### Redirect URI registration

The redirect URI registered in the Google Cloud Console must match
the application's issuer exactly. The issuer is computed as
`https://<custom_domain>` from the CDK stack, where `custom_domain`
is `channel.<HOSTED_ZONE_NAME>` in prod or
`channel-<env_name>.<HOSTED_ZONE_NAME>` otherwise.

### Rotation procedure

**Client secret rotation** (low-impact — sessions persist):

1. In the Google Cloud Console, generate a new client secret and
   keep the old one active until cut-over completes.
2. Update SSM:
   ```bash
   aws ssm put-parameter \
     --name /channel/google-client-secret \
     --value "$NEW_SECRET" --type String --overwrite
   ```
3. Bounce the Lambda so the `lru_cache` re-reads.
4. Once new logins succeed, delete the old secret in the console.

**Allowlist update** (no cold start needed):

```bash
aws ssm put-parameter \
  --name /channel/allowed-emails \
  --value '["alice@example.com","bob@example.com"]' \
  --type String --overwrite
```

The change is picked up within 60 seconds (TTL cache).

**Client ID rotation** is rare and effectively a re-registration —
treat it as a new OAuth setup: update SSM, bounce the Lambda, and
update the redirect URI in the Google Cloud Console if it changed.

## Management JWT contract

The management UI stores a signed JWT in browser `localStorage` and
presents it as a `Bearer` token on every API call. The signature and
claims are self-contained, but validation also performs a single
`DENY#{jti}` denylist point-read (#240) so a revoked (logged-out)
session is rejected before its `exp` rather than staying live for the
rest of its window.

This is the short-lived **access** token. The long-lived credential is
the opaque refresh token (`REFRESH#{sha256(token)}`, #290), exchanged at
`POST /auth/refresh` (#291) — see
[Refresh tokens](#refresh-tokens) below.

| Field | Value |
| --- | --- |
| Algorithm | HS256 (signed with the JWT signing secret above) |
| Issuer (`iss`) | matches `STARTER_ISSUER` |
| Subject (`sub`) | user's email |
| `email` claim | user's email |
| `display_name` claim | from Google ID token (`name`) |
| `role` claim | `"admin"` or `"user"` (set at login from `is_admin_email`) |
| `typ` claim | `"mgmt"` (distinguishes from API access tokens) |
| `jti` claim | unique per-token id (`uuid4().hex`); the `DENY#{jti}` denylist key used for revocation |
| `iat` / `exp` claims | seconds since epoch; TTL = 1 hour |
| TTL constant | `MGMT_JWT_TTL_SECONDS = 3600` (1 hour) in `src/channel/auth/tokens.py` |
| Revocation | logout writes `DENY#{jti}` (DynamoDB `ttl` = the token's `exp`); `decode_mgmt_jwt` rejects a denied `jti`, surfacing as HTTP 401 (#240 / #291) |
| Browser storage key | `localStorage["channel_mgmt_token"]` — JSON `{access_token, expires_at}`, or a **bare JWT** between a web login and the first SPA save (the login-completion page writes the token directly; `expires_at` is then derived from the `exp` claim). Reads still fall back to the pre-rename `starter_mgmt_token` (#260/#295) |
| Issuer (server) | `src/channel/auth/tokens.py` (`issue_mgmt_jwt`) via `src/channel/auth/mgmt_auth.py` |
| Validator (server) | `src/channel/auth/tokens.py` (`decode_mgmt_jwt`) via `src/channel/api/_auth.py` (`require_mgmt_user`, `require_admin`) |
| Issuer (UI) | server-side `mgmt_callback` returns an HTML redirect that writes the token |
| Sender (UI) | `ui/src/api.js` reads the token, silently renews it via `POST /auth/refresh` when it is within 5 minutes of expiry, and sets the `Authorization` header |

### Validation rules

`decode_mgmt_jwt` enforces, in order:

1. Signature verifies under the active JWT signing secret.
2. `iss` claim equals `CHANNEL_ISSUER`.
3. `exp` claim is in the future.
4. `typ` claim equals `"mgmt"` — an OAuth 2.1 access token cannot be
   replayed as a management session, and vice versa.
5. `jti` is not on the `DENY#{jti}` revocation denylist (#240), via a
   single strongly-consistent point read. A token minted before #240
   carries no `jti` and skips the read.

Failures raise `JWTError`, which `require_mgmt_user` translates to
HTTP 401. `require_admin` adds an HTTP 403 if `role != "admin"`.

Step 5 lives inside `decode_mgmt_jwt` rather than in the caller (#291)
so that every present and future consumer of a management JWT inherits
revocation-awareness — one fail-closed enforcement point instead of a
second call each caller has to remember. It is what lets a stolen or
logged-out token be killed before its `exp` passes. A denylist *read
failure* is not a `JWTError` and therefore is not translated to 401: it
propagates as a 500, so revocation can never be bypassed by inducing
DynamoDB errors.

The UI renews the access token before it expires (#295), so a 401 from
the API means the silent refresh could not be completed either. It is
therefore terminal: `api.endSession()` clears both storage keys and
routes to `/app/login`; see `ui/src/api.js`.

### Refresh tokens

The access token above is short-lived by design (1 hour). The
credential that survives across sessions is the **refresh token**: an
opaque 256-bit random string, never a JWT, persisted only as
`REFRESH#{sha256(token)}` so a database disclosure yields nothing
usable (#290).

`POST /auth/refresh` (#291) exchanges one for a fresh access JWT and a
rotated successor. It is **unauthenticated** — by the time a client
needs to refresh, the access token it holds has usually expired, so
gating the route on one would deadlock the flow.

| Property | Value |
| --- | --- |
| Web transport | `channel_refresh` cookie — `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/auth/refresh`, `Max-Age` = the family's `absolute_expires_at` |
| Desktop/mobile transport | request body `{"refresh_token": ...}`; successor returned in the JSON body |
| CSRF control | non-empty `X-Channel-Refresh` header required, value never inspected (empty is treated as absent); missing → HTTP 403, checked before the token is consumed |
| Rotation | hard — every use revokes the presented row and mints a successor; replaying a consumed token revokes the whole device family (RFC 9700 §4.14.2) |
| Failure response | uniform HTTP 401 `Invalid or expired refresh token`; the specific reason is logged, never returned |
| Cookie hygiene | a rejected cookie-transport request also clears the cookie, so a browser cannot replay a dead token |

The web transport never returns the refresh token in the response body
— that would defeat `HttpOnly`. The two CSRF layers are independent:
`SameSite=Strict` keeps the cookie off cross-site requests at all, and
the custom header cannot be set by a forged form or image POST without
a CORS preflight this API does not grant.

Revoking a refresh token is a conditional write on its row
(`revoked=true`); `revoke_all_user_refresh_tokens` is the "sign out
everywhere" path. Note the known window documented with
`_revoke_refresh_family` in `src/channel/storage.py`: family revocation
reads an eventually-consistent GSI, so it is best-effort with respect
to concurrent mints, bounded by the family's unchanged
`absolute_expires_at`.

### Rotation procedure

Management JWTs are session credentials, not long-lived secrets. An
individual token is revoked by adding its `jti` to the `DENY#{jti}`
denylist (#240) — this is what logout does, and it takes effect on
the next request. To invalidate *all* outstanding tokens at once,
rotate the signing secret (see the
[JWT signing secret](#jwt-signing-secret) section). Remove a user by
deleting their email from `ALLOWED_EMAILS` so they cannot log in
again; existing sessions then end by per-token denylisting, by a
signing-secret rotation, or by waiting out the 1-hour access TTL.
Removing the email does **not** by itself stop a refresh — role is
recomputed from the allowlist on every refresh, but membership is not
re-checked — so revoke the user's refresh tokens
(`revoke_all_user_refresh_tokens`) to end their sessions for good.

## Rotation quick reference

Summary of all secret rotations covered above, in roughly increasing
order of operational impact:

| Secret | Impact | Cold start required | TTL pickup |
| --- | --- | --- | --- |
| `ALLOWED_EMAILS` | Adds/removes a user; takes effect within 60s | No | 60 seconds (allowlist cache) |
| Origin-verify secret | Brief CloudFront/Lambda mismatch window | Yes (Lambda) + redeploy (CloudFront) | Immediate after redeploy |
| Google client secret | Logged-in sessions persist; new logins use new secret | Yes (Lambda) | Immediate after cold start |
| Google client ID | Effectively a re-registration | Yes (Lambda) | Immediate after cold start |
| JWT signing secret | **All outstanding tokens invalidated** (API + UI sessions) | Yes (Lambda) | Immediate after cold start |

General playbook for any SSM-backed secret:

1. Generate a new value (e.g. `openssl rand -hex 32`).
2. `aws ssm put-parameter --name <path> --value "$NEW" --type String --overwrite`.
3. For CloudFront-injected values (origin-verify), redeploy the CDK
   stack so the dynamic reference re-resolves.
4. Force a Lambda cold start (publish a new version, or touch an
   environment variable) so `functools.lru_cache` discards the old
   value.
5. Verify the new value is in effect: hit the API with a fresh token,
   confirm 200; for origin-verify, confirm a direct Function URL
   request without the header is rejected. The basic header check
   already enforces today once the secret is rotated away from the
   placeholder; #16 only changes the placeholder/SSM-error
   fail-closed behaviour.

### Future fail-closed validation (#16)

After [issue #16](https://github.com/warlordofmars/agentcore-starter/issues/16)
lands, the Lambda will refuse to start when any of the SSM-backed
secrets above still hold the `CHANGE_ME_ON_FIRST_DEPLOY` placeholder.
The startup-time check will run before the FastAPI app accepts any
request, so a half-configured deploy fails the health check rather
than degrading silently. Until then, treat the placeholder values
as a "first-deploy task" — rotate every SSM parameter with placeholder
content immediately after the initial CDK deploy.
