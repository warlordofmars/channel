# #183 — Code execution via Lambda sandbox (design)

> **Status:** brainstormed and approved 2026-06-07.
> **Implements:** [#183](https://github.com/warlordofmars/channel/issues/183) — epic #128 part C.
> **Depends on:** chassis [#181] (merged) + web-search [#182] (merged).

## Goal

Ship code execution as the second concrete tool on top of the chassis.
A model turn that calls `code_exec(code)` invokes a per-call AWS Lambda
sandbox running the code in a Python subprocess, returns stdout / stderr /
exit code / duration / any saved images from `/tmp`, and surfaces that
result in the Conversation view via a `ToolResultBlock kind="code-output"`
branch — monospace stdout (collapsible >5 lines), stderr in a `<details>`,
inline images, and a meta line.

This closes the third concrete capability needed for epic #128. After
this merges + the `#128-spike` design doc lands, epic #128 closes.

## Out of scope

- **GPU / accelerator runtimes.** Lambda only.
- **Persistent filesystem between calls.** Each invocation gets a wiped
  `/tmp`.
- **Hard internet-egress isolation.** Sandbox is NOT in a VPC, so it
  inherits AWS's managed-runtime outbound network — meaning user code
  CAN reach the public internet. The IAM boundary (no DDB / S3 /
  Bedrock / Secrets / SSM grants) is the load-bearing isolation, not
  the network boundary. Real no-egress containment (private subnets
  with no NAT route + egress-disabled security group) is a follow-up;
  for v1 the tool docstring nudges the model away from gratuitous
  outbound calls.
- **Sandbox-to-DDB / Bedrock / S3.** Sandbox IAM grants nothing beyond
  CloudWatch Logs writes.
- **Per-user / per-chat rate-limiting.** The chassis's per-chain
  `tool_calls_max=8` budget + reserved-concurrency 5 on the sandbox
  function are the blast-radius bounds for v1.
- **Languages other than Python.** Single language. The tool arg is
  `code: str` interpreted as Python source.

## Architecture

Two new units, both small:

1. **Sandbox Lambda** — separate function from the API Lambda, with its
   own IAM role and no Channel-data access.
2. **`code_exec` Strands tool** — `@tool` wrapper invoking the sandbox
   Lambda synchronously, gated by `STARTER_CODE_EXEC_ENABLED`.

SSE protocol is unchanged. The chassis's existing `tool_started` /
`tool_finished` / `tool_error` events carry code-exec results. The
existing `ToolResultBlock` component (scaffolded in #181 PR-3 for
exactly this) grows a `kind="code-output"` branch.

```text
┌──────────────┐     invoke(RequestResponse)     ┌──────────────┐
│   API Lambda │  ─────────────────────────────► │ Sandbox Lambda│
│   (Strands   │       JSON {code: str}          │  (Python      │
│   tool       │                                 │  subprocess)  │
│   wrapper)   │  ◄───────────────────────────── │               │
└──────────────┘   JSON {stdout, stderr, ...}    └──────────────┘
       │
       │ tool_result event → SSE
       ▼
   useChatStream → step.payload + kind="code-output"
       │
       ▼
   ToolResultBlock kind="code-output"
```

## Sandbox handler

File: `src/channel/sandbox/handler.py`

```python
def lambda_handler(event, _ctx):
    code = event.get("code", "")
    if not isinstance(code, str) or not code:
        return _err("empty_code")
    _wipe_tmp()
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True,
            timeout=_SUBPROCESS_TIMEOUT_SEC,
            cwd="/tmp",
        )
        stdout, stderr = proc.stdout, proc.stderr
        exit_code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or "", exc.stderr or ""
        exit_code = -1
        timed_out = True
    duration_ms = int((time.monotonic() - start) * 1000)
    stdout, stdout_trunc = _cap(stdout, _STDOUT_CAP)
    stderr, stderr_trunc = _cap(stderr, _STDERR_CAP)
    images = _harvest_tmp_images()
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "truncated": stdout_trunc or stderr_trunc,
        "timed_out": timed_out,
        "images": images,
    }
```

### Curated package set

Baked into the Lambda zip via CDK `BundlingOptions`:

- `numpy`, `pandas`, `matplotlib`
- `requests`, `httpx`, `python-dateutil`
- Plus the stdlib

(`scipy` was in the original spec but cut from v1 — the full sci-stack
zip exceeded Lambda's 250 MB unzipped cap. Tracked as a follow-up to
move scipy to a Lambda layer if real workflows hit the gap.)

Total zip target ~190 MB (under Lambda's 250 MB unzipped limit).
Driven from `src/channel/sandbox/requirements.txt` so the package list
is auditable in code review and Trivy can scan it.

### Constants

| Constant | Value | Rationale |
|---|---|---|
| `_STDOUT_CAP` | 20 KB | Per issue body. Bounded context cost on long chains. |
| `_STDERR_CAP` | 5 KB | Per issue body. Errors are usually short; tracebacks compress well. |
| `_SUBPROCESS_TIMEOUT_SEC` | 270 | 30 s short of the 5-min Lambda timeout so the handler can serialize a `{timed_out: true}` response. |
| `_MAX_IMAGES` | 3 | Bounded inline payload. |
| `_MAX_IMAGE_BYTES` | 1 MB each | Lambda response payload cap is 6 MB; 3×1 MB images + stdout/stderr fits comfortably. |
| Truncation marker | `…[truncated, N more bytes]` | Same shape as web-search clamping. |

### `/tmp` policy

`/tmp` is Lambda's default 512 MB writable scratch. We:

- **Wipe at handler entry** — close warm-container leakage between
  invocations (different users, same container).
- **Set `cwd="/tmp"`** for the subprocess so relative paths in user code
  land in scratch.
- **Scan post-exec** for `*.png` / `*.jpg` / `*.jpeg` / `*.svg`, first
  3 by mtime, each capped at `_MAX_IMAGE_BYTES`, base64-encoded into
  the response.

### Containment posture

- **No VPC** → no internet egress (Lambda outside a VPC cannot reach
  the public internet from the sandbox's perspective, by AWS network
  policy)
- **No persistent filesystem** beyond `/tmp` (wiped per call)
- **No IAM grants** beyond CloudWatch Logs writes — sandbox cannot
  read DDB, write S3, invoke Bedrock, decrypt SSM
- **`subprocess.run`** isolates user code from the handler's own
  Python interpreter; a SyntaxError or `sys.exit()` in user code
  doesn't kill the handler

### Behaviors at the boundaries

| Input | Behavior |
|---|---|
| Empty `code` string or non-string | Return `{status: "error", content: [{text: "empty_code"}]}` |
| `sys.exit(7)` in user code | Return `exit_code=7`, normal success path; model decides what to do |
| `1/0` in user code | Stderr contains traceback, `exit_code != 0`, normal success path |
| Subprocess hits 270 s timeout | Return partial stdout/stderr + `exit_code=-1` + `timed_out=true` |
| User code writes 1 MB to stdout | Truncate at 20 KB + marker, `truncated=true` |
| User code writes 4 PNGs to /tmp | First 3 by mtime returned; 4th silently dropped |
| User code writes a 2 MB PNG | Skipped (over per-image cap); not surfaced in response |

## Strands tool wrapper

File: `src/channel/agents/tools/code_exec.py`

```python
@functools.lru_cache(maxsize=1)
def _get_lambda_client():
    import boto3  # noqa: PLC0415
    return boto3.client("lambda")


@tool
def code_exec(code: str) -> dict[str, Any]:
    """Execute Python code in a sandboxed subprocess. Returns stdout, stderr, exit_code.

    Use when you need to compute, transform data, analyze a CSV, plot
    something, or run a quick simulation. The environment has numpy,
    pandas, matplotlib, requests, httpx, python-dateutil. (scipy was
    excluded from v1 — exceeded Lambda's 250 MB unzipped cap.)

    To return a plot, save it to `/tmp/<name>.png` — the user will see
    the image inline. Up to 3 images per call, ≤1 MB each.

    Network access: NONE (no VPC, no internet egress).
    Filesystem: writable /tmp only; wiped between calls.
    Timeout: 270 seconds.
    stdout cap: 20 KB; stderr cap: 5 KB.

    Args:
        code: The Python source to execute.
    """
    arn = os.environ.get("STARTER_CODE_EXEC_LAMBDA_ARN")
    if not arn:
        return _error_result("not_configured")
    client = _get_lambda_client()
    try:
        resp = client.invoke(
            FunctionName=arn,
            InvocationType="RequestResponse",
            Payload=json.dumps({"code": code}).encode(),
        )
    except client.exceptions.TooManyRequestsException:
        return _error_result("rate_limit")
    except botocore.exceptions.ClientError as exc:
        logger.warning("code_exec.client_error %r", exc)
        return _error_result("invoke_failed")

    if "FunctionError" in resp:
        return _error_result("sandbox_init_error")

    payload = json.loads(resp["Payload"].read())
    return payload
```

### Error taxonomy

Strands-`ToolResult`-shaped (`{"status": "error", "content": [{"text": "<reason>"}]}`) so `translate_event` extracts the reason as `error_type` — same shape as `web_search` (PR #225 contract).

| `error_type` | Cause |
|---|---|
| `not_configured` | `STARTER_CODE_EXEC_LAMBDA_ARN` unset (kill switch / dev without infra) |
| `rate_limit` | `TooManyRequestsException` — reserved concurrency exhausted |
| `invoke_failed` | Generic boto3 `ClientError` invoking Lambda |
| `sandbox_init_error` | `FunctionError` in response (sandbox cold-start crash, OOM, etc.) |

**Non-zero exit is NOT an error.** Returning `{exit_code: 7, stderr: "..."}` is a successful tool call — the model gets stderr and decides what to do. This mirrors the chassis's distinction between "tool failed" (SSE `tool_error`) and "tool succeeded with payload the model can interpret" (SSE `tool_finished`).

### Registration

`build_agent()` in `src/channel/agents/chat_agent.py` includes `code_exec` in `tools=[...]` iff `os.getenv("STARTER_CODE_EXEC_ENABLED") == "1"`. Same kill-switch shape as `STARTER_WEB_SEARCH_ENABLED`.

## CDK construct

File: `infra/stacks/channel_stack.py` — new construct + wiring.

```python
sandbox_role = iam.Role(
    self, "CodeExecLambdaRole",
    assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
    managed_policies=[
        iam.ManagedPolicy.from_aws_managed_policy_name(
            "service-role/AWSLambdaBasicExecutionRole"
        ),
    ],
)

code_exec_fn = lambda_.Function(
    self, "CodeExecLambda",
    function_name=f"channel-{env_name}-code-exec",
    runtime=lambda_.Runtime.PYTHON_3_13,
    handler="channel.sandbox.handler.lambda_handler",
    code=lambda_.Code.from_asset(
        "src",
        bundling=cdk.BundlingOptions(
            image=lambda_.Runtime.PYTHON_3_13.bundling_image,
            command=[
                "bash", "-c",
                "pip install -r channel/sandbox/requirements.txt -t /asset-output && "
                "cp -r channel /asset-output/channel",
            ],
        ),
    ),
    timeout=cdk.Duration.minutes(5),
    memory_size=1024,
    reserved_concurrent_executions=5,
    role=sandbox_role,
    snap_start=lambda_.SnapStartConf.ON_PUBLISHED_VERSIONS,
    environment={"PYTHONHASHSEED": "0"},
    log_retention=logs.RetentionDays.ONE_WEEK,
)

code_exec_fn.grant_invoke(api_fn)
api_fn.add_environment(
    "STARTER_CODE_EXEC_LAMBDA_ARN",
    code_exec_fn.current_version.function_arn,
)
api_fn.add_environment("STARTER_CODE_EXEC_ENABLED", "1")
```

### Rationale

- **Python 3.13 + SnapStart on published versions** — SnapStart is
  available on Python 3.13. Without SnapStart, cold-start with the
  sci-stack imports is multiple seconds.
- **`memory_size=1024`** — pandas/matplotlib need headroom; Lambda CPU
  scales linearly with memory, so this also makes the subprocess
  faster.
- **`reserved_concurrent_executions=5`** — bounds the blast radius of
  accidental abuse. The chassis cap (`tool_calls_max=8` per chain)
  bounds per-chain cost; reserved concurrency bounds global cost.
- **Separate IAM role with `AWSLambdaBasicExecutionRole` only** — even
  if user code escapes the subprocess, the sandbox has no access to
  any Channel data.
- **`log_retention=ONE_WEEK`** — code bodies may contain user data;
  short retention reduces the surface area.

### CDK unit-test assertions

`tests/unit/test_channel_stack.py`:

- Construct exists, runtime `python3.13`, SnapStart configured
- Sandbox role has NO statement granting `dynamodb:*`, `s3:*`,
  `bedrock:*`, `secretsmanager:*`, or `ssm:*` (walk the synthesized
  `AWS::IAM::Policy` documents; same pattern as the Exa SSM grant
  check from PR #222)
- API Lambda role has `lambda:InvokeFunction` on the sandbox function
  ARN
- `STARTER_CODE_EXEC_LAMBDA_ARN` + `STARTER_CODE_EXEC_ENABLED` env
  vars wired on the API Lambda
- `reserved_concurrent_executions` is exactly `5`

## SPA rendering

### `ToolResultBlock` extension

File: `ui/src/app/ToolResultBlock.jsx` — add the `kind="code-output"`
branch alongside the existing default text-summary path.

```jsx
function CodeOutputBlock({ payload }) {
  const [expanded, setExpanded] = useState(false);
  const stdoutLines = (payload.stdout || "").split("\n").length;
  const collapsed = !expanded && stdoutLines > 5;
  const shown = collapsed
    ? payload.stdout.split("\n").slice(0, 5).join("\n")
    : payload.stdout;
  return (
    <div className="tool-result-block" data-kind="code-output">
      {payload.stdout && (
        <div className="code-output-pane">
          <div className="code-output-toolbar">
            <span>stdout</span>
            <button onClick={() => copyToClipboard(payload.stdout)}>Copy</button>
            {stdoutLines > 5 && (
              <button onClick={() => setExpanded(v => !v)}>
                {expanded ? "Collapse" : `Show all ${stdoutLines} lines`}
              </button>
            )}
          </div>
          <pre className="code-output-text">{shown}</pre>
        </div>
      )}
      {payload.stderr && (
        <details className="code-output-stderr">
          <summary>stderr ({payload.stderr.length} bytes)</summary>
          <pre>{payload.stderr}</pre>
        </details>
      )}
      {payload.images?.map((img, i) => (
        <img key={i} src={`data:${img.mime};base64,${img.b64}`} alt={`output ${i}`} />
      ))}
      <div className="code-output-meta">
        exit {payload.exit_code} · {payload.duration_ms}ms
        {payload.truncated && " · output truncated"}
        {payload.timed_out && " · timed out at 270s"}
      </div>
    </div>
  );
}
```

### Discrimination point

`strands_sse.translate_event` already maps Strands tool events to SSE
event types. We extend it to recognize `tool_result` events where
`tool_name === "code_exec"` and emit `{kind: "code-output", payload}`
on the SSE wire instead of stringifying the result into `summary`.

In `useChatStream.patchToolStep`, the `tool_finished` branch checks for
`kind` on the event and routes to `step.payload` instead of (or
alongside) `step.summary`.

In `Conversation.jsx` the existing line
`{step.summary && <ToolResultBlock kind={step.kind} summary={step.summary} />}`
extends to pass `payload` too:
`<ToolResultBlock kind={step.kind} summary={step.summary} payload={step.payload} />`.

### Styling

All colours / radii / shadows / fonts via CSS-vars from `channel.css` per
the project's UI conventions. New rules land in `app.css` under a
`/* code-output renderer */` section: `.code-output-pane`,
`.code-output-toolbar`, `.code-output-text`, `.code-output-stderr`,
`.code-output-meta`. Inline images get a `max-width: 100%` + `border-radius: var(--radius-md)` + `border: 1px solid var(--border)`.

## Testing

### Unit (Python)

- `tests/unit/test_sandbox_handler.py` — handler behaviors at the
  boundaries listed under "Behaviors at the boundaries" above.
- `tests/unit/test_tools_code_exec.py` — Strands wrapper, error
  taxonomy round-trip through `translate_event`, lazy-loader cache
  discipline.
- `tests/unit/test_chat_agent.py` — kill switch (extends existing
  file).
- `tests/unit/test_channel_stack.py` — CDK assertions (extends
  existing file).
- `tests/unit/test_strands_sse.py` — `code_exec` discrimination.

### Frontend (vitest)

- `ToolResultBlock.test.jsx` — 5 cases for the code-output branch.
- `useChatStream.test.js` — `code_exec` `tool_finished` populates
  `step.payload` + `step.kind`.

### Integration

None new. Sandbox doesn't touch DynamoDB.

### E2e (`tests/e2e/test_code_exec.py` — new)

Against deployed dev env, using existing `_http_helpers`:

- **Happy path** — chat asks "what is 7*6?" → assistant uses
  `code_exec` → final reply contains `42`
- **Containment** — chat asks for code that connects to `1.1.1.1:80` →
  `tool_finished` shows stderr including connection failure
- **Length cap** — chat asks for 1 million 'x' characters →
  `truncated: true`, stdout exactly at 20 KB
- **Image return** — chat asks for a matplotlib chart of `[1,2,3]` →
  `images[0]` non-empty with PNG mime

Rate-limit / kill-switch tests punted to unit level.

### Coverage

100% per `CLAUDE.md`. Handler error branches (timeout, truncation,
image cap, empty code) all covered by unit tests. Strands wrapper
error-taxonomy branches covered by parameterized tests mirroring
`test_tools_web_search.py`.

## Operating decisions tracked here

| Decision | Rationale |
|---|---|
| Python only (`code: str`), no `language` arg | Single language keeps the surface tiny. Multi-language is a follow-up. |
| Sci-stack baked in (not via Lambda layer) | Keeps deploy under our own control. No region-specific layer ARN. Trivy scans the lockfile. |
| 270s subprocess timeout (vs 290s) | 30s buffer for response serialization + image base64 encoding. Headroom > saving 20s of run time. |
| /tmp wiped at entry, NOT at exit | Wiping at exit doesn't protect *this* invocation from prior warm-container state. Entry is the correct point. |
| Images written to `/tmp/*.png`, harvested post-exec | Lower friction than expecting the model to base64-print. Convention is explicit in the tool docstring. |
| Image cap 3×1 MB per call | Bounded inline SSE payload. Larger artifacts are a follow-up (e.g. S3 + presigned URL). |
| `reserved_concurrent_executions=5` | Bounded global cost. Tune up later if real usage warrants. |
| Log retention 1 week | Code bodies could be sensitive. Standard for handler logs. |
| `STARTER_CODE_EXEC_ENABLED=1` shipped on by default in CDK | Same posture as `STARTER_WEB_SEARCH_ENABLED`. Kill-switch usable via env override at runtime. |

## What this closes

Epic #128 closing condition (from the strategy spec):

- [x] #181 chassis merged
- [x] #182 web search merged
- [ ] #183 this work merged
- [ ] #184 spike design doc landed in `docs/superpowers/specs/`

When #183 merges and the #184 doc lands, epic #128 closes.

## Risks and follow-ups

- **Lambda 250 MB unzipped limit** — sci-stack zip is ~80 MB so we have
  headroom, but adding `polars` / `scikit-learn` later could push us
  over. Mitigation if needed: switch to Lambda layer for the heavy deps.
- **Cold start of unused warm pool slots** — SnapStart helps but isn't
  free. If real-world latency is a problem, consider provisioned
  concurrency on the sandbox (not just reserved).
- **No quotas per user** — heavy abuse from one user could exhaust the
  reserved-concurrency pool for all users. Adequate for v1; follow-up
  if observed.
- **Image follow-up** — S3 + presigned URL flow for >1 MB plots / PDFs
  is worth filing as a separate issue under epic #128's "shipped
  capability evolution" track.
- **Multi-language follow-up** — JavaScript or shell as additional
  `language` arg values; same Lambda would handle the dispatch.

## CLAUDE.md updates

No new product-decision bullets. The sandbox is a concrete tool on top
of the chassis whose architectural principles (one tool per file under
`agents/tools/`, error_type taxonomy via Strands-ToolResult shape,
kill-switch env var, lazy-loaded boto3 client) are already established
by chassis [#181] + web-search [#182]. No new invariant required.
