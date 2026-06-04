# ADR-0010: Electron desktop release pipeline + macOS signing
Date: 2026-05-31  
Status: Accepted

## Context

Sub-project A (#23) shipped an unsigned Electron wrapper that bundles
the React SPA and authenticates via a loopback OAuth flow. Sub-project B
(#33) needed to take that unsigned build path and turn it into a real,
auto-updating release pipeline. Five design choices made in the brainstorm
phase needed to be captured durably, and four operational incidents that
surfaced during the implementation deserve to be recorded so a future
maintainer (or future-me) doesn't re-derive them by getting bitten again.

The audience driving the *design* choices was "the author plus a handful
of vouched testers" — a small, known group. A different audience (public
download from the marketing site, or B2B/enterprise) would change every
one of the decisions below.

The implementation landed across six sub-issues (#37–#42) plus eight
follow-up hotfix PRs over a single intense day. The hotfixes are not
embarrassments; they are the natural shape of integrating against
real-world managed services (Apple notary, AWS WAF) whose default
behavior makes assumptions about the integrator that didn't match us.

## Decision

### Design choices

**1. macOS-only signing in this round; Windows and Linux deferred.**
Apple Gatekeeper on macOS 14+ refuses to open an unsigned `.app` from
the internet without manual override clickthrough, so Mac signing earns
its keep on day one for even a small tester audience. Windows produces
a SmartScreen "Unknown publisher" warning that testers can click past,
and Linux .deb/.rpm/AppImage don't have an OS-level distribution-time
signing requirement at all. Windows EV certs (~$300/yr from a CA, or
Azure Trusted Signing at lower cost) and a per-distro Linux package-
signing story are deferred until the audience grows.

**2. Two channels (`latest` + `dev`) mapped 1:1 to the existing CDK env split.**
`main` → `latest` channel served from
`https://channel.warlordofmars.net/updates/latest/`.
`development` → `dev` channel served from
`https://channel-dev.warlordofmars.net/updates/dev/`.
The CDK env split (`ChannelStack` + `ChannelStack-dev`) already owns
separate CloudFront distributions, IAM, and S3 buckets — mapping
channels 1:1 to envs requires zero cross-env coupling. A three-channel
topology (`stable` / `beta` / `dev`) was rejected as over-engineered for
the current audience. The update feed URL is *baked into each desktop
build at build time* via `electron-builder.yml`'s `publish.url` rather
than chosen at runtime, so a production build only ever checks the prod
URL and a dev build only ever checks the dev URL. No runtime channel-
switching UI is needed; no shared state between envs.

**3. GitHub Actions repo secrets for the signing credentials (not SSM / Secrets Manager).**
The five Apple credentials (`APPLE_CERT_P12_BASE64`, `APPLE_CERT_PASSWORD`,
`APPLE_API_KEY_P8_BASE64`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER_ID`) are
build-time only — Lambda never sees them. AWS SSM Parameter Store and
Secrets Manager are appropriate for runtime secrets like
`/channel/{env}/jwt-secret`, not build-time CI inputs. Putting build
secrets in SSM would force the GitHub runner to assume an AWS role just
to fetch them, adding IAM complexity for no audit-trail benefit
(GH already audits secret access in workflow logs).

**4. Same S3 bucket + new `/updates/*` CloudFront prefix + new behavior.**
Auto-update artefacts (`latest-mac.yml`, `Channel-<ver>-mac.zip`,
`Channel-mac.dmg`) live in the existing UI bucket under prefix
`updates/<channel>/`, fronted by a new `/updates/*` behavior on the
existing distribution. No separate distribution, no separate bucket,
no separate subdomain. Smallest infra delta; reuses the existing OAC +
WAF + cert. Blast radius is contained via path-scoped behaviors — a
bad cache config on `/updates/*` cannot affect `/api/*`, `/docs*`, or
`/`. The alternative (separate `updates.channel.warlordofmars.net`
distribution) would double CloudFront cost for no security benefit.

**5. GitHub Releases for first-install downloads; S3 for auto-update only.**
The public `Download.jsx` page links to
`https://github.com/warlordofmars/channel/releases/latest/download/<file>`
for all platforms. The S3 `/updates/<channel>/` tree holds the
auto-update manifest + `.zip` + `.blockmap` plus a stable
`Channel-mac.dmg` for dev-channel testers (private link, since the
public dev tag still serves the unsigned matrix builds for the
moment). Avoids dual-source-of-truth for the same `.dmg`. GitHub
Releases are indexed by changelog, versioned URLs, and already serve
as the public release surface; S3 stays lean and exists only for the
auto-update flow.

### Incidents (operational findings)

Four issues hit during implementation that the spec couldn't have
predicted. Each is worth recording because the same trap is waiting
for the next person who sets up macOS signing or builds an Electron
app behind a managed WAF.

**6. Apple's notary holds the first hardened-runtime submissions from brand-new Developer ID accounts for ramp-up review.**
Discovered when sub-issue #40's `publish-desktop-mac` job's first real
notarisation submission sat in `In Progress` for 9+ hours, then a
second sat for 5+ hours, then a third for 30+ min — all created on a
Developer ID account less than 24 hours old. A separate non-
hardened-runtime submission earlier the same day had returned an
`Invalid` verdict in ~70 seconds, proving Apple's notary service itself
was responsive; the discriminator was hardened runtime + new-account.
Apple's developer forums document this informally: the first submission
that clears typically takes 24–48h, after which subsequent submissions
return verdicts in <15 min. Multiple repeated submissions during
ramp-up can compound the throttling. The mitigation is to pause and
wait; do *not* keep resubmitting. Tracked at issue #71 with the open
checklist (wait, hello-world sanity check, draft Apple support
request, re-enable CI when verdicts return). PR #70 temporarily
disabled `publish-desktop-mac` on push so accumulating submissions
during ramp-up doesn't compound the problem.

**7. macOS 15 Sequoia enforces strict Team ID consistency across a hardened-runtime bundle.**
Discovered when an unsigned matrix-build `.dmg` installed on a tester
machine crashed at launch with:

> Library not loaded: @rpath/Electron Framework.framework/Electron Framework
> Reason: code signature ... not valid for use in process:
> mapping process and mapped file (non-platform) have different Team IDs

Older macOS tolerated a bundle where the outer binary was ad-hoc
signed (TeamID `""`) while the inner Electron Framework retained the
prebuilt's Apple Team ID. Sequoia refuses to load. Sub-project A
worked because its `electron-builder.yml` had `hardenedRuntime: false`,
which suppresses the strict check. Sub-project B's spec required
`hardenedRuntime: true` to satisfy notarisation — but that flag also
applies to the unsigned matrix builds, which then break on Sequoia.
Fix (PR #81): override `--config.mac.hardenedRuntime=false` and
`--config.mac.gatekeeperAssess=false` on the CI matrix's
`electron-builder` invocation only. The publish-desktop-mac job
inherits the YAML default of `true` and still notarises correctly.

**8. AWS WAF's `AWSManagedRulesCommonRuleSet` blocks OAuth callback URLs in query strings.**
Discovered when the desktop app's loopback OAuth flow opened
`https://channel-dev.warlordofmars.net/auth/login?desktop_callback=http%3A%2F%2F127.0.0.1%3A64580%2Fcallback&state=...`
in the browser and got back the SPA's index.html with HTTP 200 — but
the desktop app expected a 302 redirect. Investigation chain:

- API Lambda log: `"GET /auth/login?desktop_callback=... HTTP/1.1" 403`
- CloudFront distribution-level `error_responses` converts `403 → /index.html (200)` to support React Router SPA fallback
- WAF sampled-requests: `Action: BLOCK / Rule: GenericRFI_QUERYARGUMENTS / URI: ... desktop_callback=http%3A%2F%2F127.0.0.1%3A64580%2Fcallback`
- After PR #82 downgraded that rule to `count`, the same URL was blocked by `EC2MetaDataSSRF_QUERYARGUMENTS` (heuristic for SSRF against the EC2 metadata service that also matches `127.0.0.1:<port>` loopback patterns)

PR #82 and #83 downgrade both rules to `count` mode via
`rule_action_overrides` on the managed rule group. CloudWatch still
records matches so genuine attacks remain observable; all other
CommonRuleSet rules (SQLi, XSS, etc.) remain at `block`. Application-
level validation in `mgmt_auth._validate_desktop_callback` enforces
loopback host + `/callback` path + no fragments — much more specific
than the WAF heuristic.

Same pattern would block any third-party OAuth callback URL passed as
a query parameter through this WAF. Worth knowing before integrating
Google OAuth's redirect_uri parameter into the public-facing API path.

**9. Two desktop-build URL bugs converged: SPA build hardcoded prod, main process couldn't read CHANNEL_API_BASE at build time.**
Discovered when the early dev desktop build connected to prod URLs
that didn't exist yet. Two separate bugs:

- The CI `desktop` matrix step hardcoded `VITE_API_BASE=https://channel.warlordofmars.net` regardless of branch.
- `desktop/main/index.js` falls back to a hardcoded prod URL via `process.env.CHANNEL_API_BASE ?? "https://channel.warlordofmars.net"`. esbuild doesn't substitute `process.env` references at build time by default, and packaged apps have no runtime `CHANNEL_API_BASE` env, so the fallback always won.

Fix (PR #74): new `desktop/scripts/build-main.js` thin esbuild wrapper
uses `--define:process.env.CHANNEL_API_BASE='"..."'` to bake the
build-time env var into the bundle. CI matrix + publish-desktop-mac
both resolve the URL per branch and pass it as both `VITE_API_BASE`
(SPA) and `CHANNEL_API_BASE` (main process).

## Consequences

### Operational

- Apple Developer Program enrolment ($99/yr) is a permanent project
  prerequisite. Renewal in mid-2031 (cert expires) is documented in
  `docs/dev/apple-signing-bootstrap.md`. The App Store Connect API key
  should be rotated annually; same runbook.
- The `publish-desktop-mac` CI job adds ~8–15 min to every push to
  `main`/`development` once re-enabled. Notarisation latency dominates;
  the rest is fast. Apple's expected response time after the new-account
  ramp-up clears is <15 min; if a submission exceeds 1h, that signals
  an Apple infra issue (or another ramp-up event after a long quiet
  period) and warrants a support ticket.
- The job is currently disabled (#70) pending the Apple ramp-up
  clearing (#71). Re-enable by reverting the `if: false` flip once
  `xcrun notarytool history` shows any of the three stuck submissions
  flip from `In Progress` to `Accepted` or `Invalid`.

### Future-proofing

- Windows + Linux signing will be a separate sub-project (size:l
  estimated) when the audience grows or a tester explicitly complains
  about a SmartScreen prompt. The matrix's per-platform structure
  already accommodates signed targets; what's missing is the cert
  procurement + per-platform signing infrastructure.
- The same WAF rule overrides (#82 + #83) protect any future OAuth
  callback parameter on this API (Google's `redirect_uri`, any SSO
  provider's callback). The override is *not* desktop-specific; it
  unblocks a broader class of legitimate OAuth flows that the managed
  rule set would otherwise treat as RFI/SSRF.
- The `hardenedRuntime` override (#81) is a *matrix-only* CLI flag, not
  a YAML change. Production signed builds always get the YAML default
  (`true`). Keep this seam — moving the override into YAML would re-
  break notarisation.
- The build-time URL baking (#74) introduces a small piece of build
  tooling (`desktop/scripts/build-main.js`). When adding new
  environment-driven constants to the main process bundle, extend that
  script's `define` block rather than scattering `--define` flags
  across CI YAML.

### Three-channel topology is the natural next step if needed

If a `beta` channel ever becomes useful (release candidates, a wider
tester pool than dev but smaller than prod), the addition is:

- A new branch (e.g. `staging`) and a new CDK env (e.g. `ChannelStack-beta`)
- A new entry in `desktop/main/updater.js`'s `FEED_URL_BY_CHANNEL` map
- A new `if [[ "${{ github.ref }}" == "refs/heads/staging" ]]` arm in `publish-desktop-mac`'s `Resolve channel + env` step

No core architecture changes — the 1:1 channel↔env mapping (decision 2)
is designed for this kind of additive growth.
