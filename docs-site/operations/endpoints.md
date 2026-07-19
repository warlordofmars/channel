# Operational endpoints

Two operational endpoints sit outside the normal authenticated management
API surface: `/health` (Lambda warm-up / liveness probe) and
`/api/csp-report` (browser CSP violation receiver). Both are
unauthenticated by design. This page documents what they accept, what
they return, who can call them, and what their abuse-protection
posture looks like today vs after the planned hardening lands.

## `GET /health`

Lightweight liveness probe used by Lambda warm-up checks and external
uptime monitors. External monitors should hit the **CloudFront URL**,
not the raw Lambda Function URL — the app-wide origin-verify
middleware (see [Origin verification](/operations/security)) returns
`403` for any request to the Function URL that doesn't carry the
shared `X-Origin-Verify` header. Probes routed through CloudFront
get the header injected automatically.

| Field | Value |
| --- | --- |
| Method | `GET` |
| Auth | Unauthenticated |
| Path | `/health` |
| Implementation | `src/channel/api/main.py` (`@app.get("/health")`) |
| OpenAPI | Hidden (`include_in_schema=False`) |
| Response | `200 OK`, `application/json` |

### Response shape

```json
{
  "status": "ok",
  "version": "<APP_VERSION>"
}
```

`version` is computed at module import time and reflects the deployed
package version. The resolution order is: the `APP_VERSION` environment
variable if set, otherwise `importlib.metadata.version("channel")`,
otherwise the literal string `"dev"` (the tests-without-an-installed-package
case). Operators can use it as a quick sanity check that a deploy
actually rolled.

### Caching and rate limiting

CloudFront fronts the Lambda Function URL but does **not** cache
`/health` responses (the path is in the no-cache behaviour set so a
stale `200` can't mask a sick origin). No application-side rate
limit is applied — `/health` is intentionally cheap (no DynamoDB
calls, no Bedrock calls) so a flood of probes is a non-event at the
handler.

The CloudFront WebACL still applies a **global per-IP rate limit of
1000 requests / 5 minutes** (the `GlobalRateLimit` rule in
`infra/stacks/channel_stack.py`), so a single IP that floods
`/health` will be blocked at the edge before it reaches the Lambda
even though no endpoint-specific limit is defined.

## `POST /api/csp-report`

CSP violation receiver. Browsers POST violation reports here when a
resource is blocked (or would be blocked under
`Content-Security-Policy-Report-Only`). Each report emits a
`WARNING`-level log line (with the violation's directive and blocked
URI in the message string) and two `CSPViolations` EMF metric
emissions (one undimensioned counter, one with `directive` +
`blocked_domain` dimensions). See
[What gets logged and emitted](#what-gets-logged-and-emitted) below
for the full picture, including which structured fields are parsed
but not yet forwarded to CloudWatch Logs.

| Field | Value |
| --- | --- |
| Method | `POST` |
| Auth | Unauthenticated (browsers don't send credentials with CSP POSTs) |
| Path | `/api/csp-report` |
| Implementation | `src/channel/api/csp.py` (`receive_csp_report`) |
| OpenAPI | Hidden (`include_in_schema=False`) |
| Response | `204 No Content` (always — even on parse error) |

### Accepted content types

The endpoint accepts both report formats browsers actually send:

- **Legacy** (`application/csp-report`) — single JSON object with a
  top-level `csp-report` key. Sent by Chromium-family browsers when
  the page declares only a `report-uri` directive.
- **Modern** (`application/reports+json`) — JSON array of report
  envelopes, each with `type: "csp-violation"` and a `body` object.
  Sent when the page declares `report-to` (the spec replacement for
  `report-uri`).

The endpoint detects the shape from the JSON structure (object vs
array) rather than the `Content-Type` header, so reports survive
header munging by intermediaries.

### What gets logged and emitted

Each violation produces a `WARNING`-level log line and two CloudWatch
EMF metric emissions.

**Log line.** The structured JSON formatter only forwards a fixed
allowlist of `extra=` keys today (see `_JsonFormatter._EXTRA_FIELDS`
in `src/channel/logging_config.py`), so the violation fields the
handler attaches via `extra={"csp": ...}` are dropped and do **not**
appear in CloudWatch Logs. What you see in CloudWatch is just the
formatted message string, which embeds two of the most useful
fields:

```text
CSP violation: script-src blocked https://evil.example.com/foo.js
```

The handler does parse and truncate the full set of fields below
(each capped at 2 KiB) before passing them to the logger, so the
data is available on the in-memory `LogRecord` — wiring the
formatter to forward `csp.*` fields is a separate piece of work
(file an issue if you need the structured form in logs).

| Field | Source |
| --- | --- |
| `violated_directive` | `csp-report.violated-directive` (legacy) / `body.effectiveDirective` (modern) |
| `effective_directive` | `csp-report.effective-directive` (legacy) / `body.effectiveDirective` (modern) |
| `blocked_uri` | `csp-report.blocked-uri` (legacy) / `body.blockedURL` (modern) |
| `document_uri` | `csp-report.document-uri` (legacy) / `body.documentURL` (modern) |
| `source_file` | `csp-report.source-file` (legacy) / `body.sourceFile` (modern) |
| `line_number` | `csp-report.line-number` (legacy) / `body.lineNumber` (modern) |
| `column_number` | `csp-report.column-number` (legacy) / `body.columnNumber` (modern) |
| `disposition` | `csp-report.disposition` (legacy) / `body.disposition` (modern) |

**EMF metrics.** Two `CSPViolations` metrics are emitted per
violation: an undimensioned counter, and a dimensioned counter with
`directive` and `blocked_domain` dimensions. The dimensioned form is
what powers operator breakdown dashboards.

### Always returns 204

The handler returns `204 No Content` whether the body parsed cleanly,
was empty, or was malformed JSON. This is intentional — browsers
fire-and-forget CSP reports and have no use for an error response,
and returning `4xx` would surface garbage to operator dashboards
without giving the browser anything actionable.

### Current rate-limit and body-cap posture

> **Hardening planned** under the CSP-report abuse-mitigation
> proposal. The values below describe the **deployed** posture
> today; the planned hardening tightens them when it lands.

| Control | Current | Planned |
| --- | --- | --- |
| Body cap | None at the FastAPI layer (Lambda Function URL caps at 6 MB) | 8 KiB per request |
| Per-IP rate limit (endpoint-specific) | None | 60 requests / 5 minutes per source IP |
| Per-IP rate limit (edge / WebACL) | 1000 requests / 5 minutes (global `GlobalRateLimit` rule) | Unchanged — keeps applying alongside the endpoint-specific limit |
| `blocked_domain` cardinality | Unbounded (any hostname becomes a CloudWatch dimension value) | Bucketed to an allowlist; everything else collapses to `other` |

Until the endpoint-specific hardening lands, the only abuse-protection
in front of `/api/csp-report` is the WebACL global rate limit (1000
req / 5 min per IP). An attacker who stays under that ceiling can
still inflate CloudWatch costs by spamming reports with arbitrary
`blocked-uri` values. The endpoint does gate at the JSON-parse step
(malformed bodies return 204 without emitting any metric) and at the
`csp-violation` type check (modern-format reports of unrelated types
are dropped), so the attack surface is bounded to "POST a mostly-valid
report at up to 1000 req / 5 min per IP".

## See also

- [Security and secrets](/operations/security) — operational secret
  contracts and rotation procedures
