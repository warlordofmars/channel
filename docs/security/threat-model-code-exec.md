# Threat model — code-exec sandbox egress

**Status:** v1 control shipped (#249)
**Trigger:** external code review, 2026-06-11
**Owner surface:** `CodeExecLambda` in `infra/stacks/channel_stack.py`;
tool entry point `src/channel/agents/tools/code_exec.py`

## The trifecta

The code-exec sandbox shipped in #183 with IAM as its only boundary.
That was acceptable while the sandbox ran in isolation — nothing
untrusted could steer what code ran. Two later features broke that
assumption:

1. **Untrusted content reaches model context.** Web search results
   (#182), MCP tool outputs (#207), and user attachments all land in
   the model's context window. Any of them can carry adversarial
   instructions (prompt injection).
2. **The model steers tool selection.** Injected instructions can
   direct the model to call any registered tool — including
   `code_exec` — with attacker-chosen arguments.
3. **Code execution had network egress.** The sandbox Lambda ran
   outside any VPC, so AWS's managed runtime granted outbound
   internet. Attacker-chosen Python could POST anything reachable in
   context (conversation content, recalled memory, tool outputs) to
   an attacker-controlled endpoint.

Untrusted context in → code-exec out → egress. That is the classic
prompt-injection → code-execution → exfiltration trifecta, and all
three legs were live once #182 and #207 shipped. The 2026-06-11
review flagged it; #249 closes the third leg.

## v1 control (#249)

The code-exec Lambda is attached to a dedicated VPC with
**`PRIVATE_ISOLATED` subnets only — no internet gateway, no NAT
gateway, no VPC endpoints, and no route entries** beyond the implicit
`local` route. As defense in depth, the Lambda's security group is
created with `allow_all_outbound=False` and **zero egress rules**
(CDK pins the canonical no-op ICMP placeholder so CloudFormation
cannot restore the allow-all default).

Properties of this control:

- **CloudWatch logging still works** — Lambda writes logs through the
  service plane, not the VPC network path, so sandbox stdout/stderr
  and platform logs are unaffected.
- **SnapStart remains compatible** — VPC attachment is not on
  SnapStart's documented incompatibility list (provisioned
  concurrency, EFS, and >512 MB ephemeral storage are).
- **Cost is ~zero** — no NAT to pay for; the VPC, subnets, and SG are
  free resources.
- **Failure mode shifts from exfiltration to timeout** — network
  attempts inside user code no longer succeed; they hang until the
  socket timeout or the sandbox's 270-second cap. The `code_exec`
  tool docstring tells the model never to attempt network access so
  it doesn't burn the execution budget discovering this.
- **IAM remains the data-plane boundary** — the sandbox role carries
  only `AWSLambdaBasicExecutionRole` plus
  `AWSLambdaVPCAccessExecutionRole` (ENI lifecycle for the VPC
  attachment); no DynamoDB, S3, Bedrock, Secrets Manager, or SSM
  grants.

Enforced by CDK assertions in `tests/unit/test_channel_stack.py`
(`test_sandbox_lambda_attached_to_vpc_with_zero_egress_sg`,
`test_sandbox_vpc_subnets_are_isolated_with_no_internet_path`,
`test_sandbox_security_group_has_no_egress_rules`).

## Residual risks (explicitly NOT closed by this control)

1. **Timing channels.** Sandbox code can still modulate its own
   runtime (and therefore the visible duration/latency of the tool
   call) to leak low-bandwidth signals to an observer of the
   conversation. Low value against this product's data; accepted.
2. **In-VPC service-plane abuse.** The Lambda service plane itself
   (CloudWatch Logs writes via the execution role, invocation
   metadata) remains reachable by design. A compromised payload could
   encode data into log lines readable by anyone with CloudWatch
   access on the account. IAM scoping keeps this inside the account
   boundary; accepted.
3. **Exfiltration via tool-result text.** The sandbox's stdout
   returns to model context, and the model may later emit it — to the
   user, into AgentCore Memory, or as arguments to other tools with
   network access (e.g. `web_fetch`). Egress isolation of the sandbox
   cannot close a channel that flows *through the model*.
   Mitigation is out of scope here; the relevant guard is the
   chassis's META-fact policy in `CLAUDE.md` `## Product decisions`
   ("Tool payloads never persist to AgentCore Memory"), which keeps
   raw tool payloads out of the durable memory pool. Cross-tool
   laundering (sandbox output → network-capable tool input) remains
   open and is a model/policy-layer problem, not an infra one.

## Alternatives considered (2026-06-11 review)

- **Option 2 — VPC + NAT + egress filtering:** deferred until a real
  use case for network-needing runtime code exists. Pip-install at
  runtime is not one; dependencies are pre-bundled per #183.
- **Option 3 — Bedrock AgentCore Code Interpreter:** filed as a
  separate spike.
