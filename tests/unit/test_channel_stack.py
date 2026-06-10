# Copyright (c) 2026 John Carter. All rights reserved.
"""CDK template assertions for ChannelStack.

Runs as pure Python unit tests (no AWS calls). Guards env-specific
configuration that the agent-safe checklist + spec Risk #5 require:

- ``STARTER_ENABLE_DEBUG_ENDPOINTS`` must NEVER leak to the prod
  Lambda — the dev-only ``/api/_debug/*`` routes are mgmt-JWT-gated
  but a misconfigured prod with the flag set still exposes
  authenticated dev surface to production users. The flag is set in
  ``channel_stack.py`` conditionally on ``not is_prod``; this test
  catches regressions.
- The Lambda role grants the AgentCore actions Phase 7c needs
  (``CreateEvent`` / ``ListEvents`` / ``ListMemories`` /
  ``CreateMemory`` / ``GetMemory``). Without these, every chat write
  fails with ``AccessDeniedException`` and silently degrades to
  ``MemoryWriteFailures`` (which is fine for the user but breaks the
  product feature).
"""

from __future__ import annotations

import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk import assertions

# infra/app.py imports as ``from stacks.channel_stack import ChannelStack``,
# i.e. it expects ``infra/`` on sys.path. Replicate that for tests.
_INFRA = Path(__file__).resolve().parents[2] / "infra"
sys.path.insert(0, str(_INFRA))

from stacks.channel_stack import ChannelStack  # noqa: E402


def _synth(env_name: str) -> assertions.Template:
    app = cdk.App(
        context={
            "env": env_name,
            "account": "123456789012",
            "region": "us-east-1",
            "hosted_zone_id": "Z0000000000000",
            # Skip Docker-based asset bundling — these are pure
            # IaC-template assertions that don't need the Lambda
            # zip. Without this CDK invokes Docker to build the
            # uvicorn+FastAPI bundle, which fails on machines without
            # the ECR pull permissions.
            "aws:cdk:bundling-stacks": [],
        },
    )
    stack_id = "ChannelStack" if env_name == "prod" else f"ChannelStack-{env_name}"
    stack = ChannelStack(
        app,
        stack_id,
        env_name=env_name,
        hosted_zone_id="Z0000000000000",
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    return assertions.Template.from_stack(stack)


@pytest.fixture(scope="module")
def prod_template() -> assertions.Template:
    return _synth("prod")


@pytest.fixture(scope="module")
def dev_template() -> assertions.Template:
    return _synth("dev")


def _api_function(template: assertions.Template) -> dict:
    funcs = template.find_resources("AWS::Lambda::Function")
    api_fns = {key: val for key, val in funcs.items() if "ApiFunction" in key}
    assert len(api_fns) == 1, f"expected exactly one ApiFunction, got {list(api_fns)}"
    return next(iter(api_fns.values()))


def test_prod_stack_does_not_set_debug_endpoints_env_var(prod_template):
    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert "STARTER_ENABLE_DEBUG_ENDPOINTS" not in env_vars, (
        "Prod stack must NOT enable debug endpoints. See spec Risk #5."
    )


def test_dev_stack_sets_debug_endpoints_env_var(dev_template):
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("STARTER_ENABLE_DEBUG_ENDPOINTS") == "1"


def test_prod_stack_does_not_set_bypass_google_auth_env_var(prod_template):
    """#179 — the Google-auth bypass MUST stay off in prod. It only
    activates when a request carries ``?test_email=``, but a
    misconfigured prod with the flag set still hands an admin JWT to
    anyone who can guess that query param. Same risk shape as
    ``STARTER_ENABLE_DEBUG_ENDPOINTS``."""

    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert "STARTER_BYPASS_GOOGLE_AUTH" not in env_vars, (
        "Prod stack must NOT enable the Google-auth bypass."
    )


def test_dev_stack_sets_bypass_google_auth_env_var(dev_template):
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("STARTER_BYPASS_GOOGLE_AUTH") == "1"


def test_prod_stack_disables_clock_tool_env_var(prod_template):
    """#181 chassis policy P2 — the ``current_time`` smoke-test tool
    must NOT register in prod. The chassis registers the tool only
    when ``STARTER_CLOCK_TOOL_ENABLED=="1"``; prod sets it to ``"0"``
    explicitly so a future default-shift can't flip it on by accident.
    """

    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("STARTER_CLOCK_TOOL_ENABLED") == "0"


def test_dev_stack_enables_clock_tool_env_var(dev_template):
    """Non-prod envs (dev / jc / etc.) get the chassis smoke-test tool
    so the end-to-end tool path stays exercised — see #181 strategy
    spec policy P2."""

    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("STARTER_CLOCK_TOOL_ENABLED") == "1"


def test_lambda_role_grants_agentcore_write_and_lookup_actions(dev_template):
    """The IAM policy attached to the API Lambda role must grant the
    AgentCore actions Phase 7c needs. We inspect every IAM::Policy
    resource because CDK distributes statements across multiple
    Policy resources at synth time."""

    policies = dev_template.find_resources("AWS::IAM::Policy")
    # IAM action prefix is ``bedrock-agentcore:`` for both planes — see
    # channel_stack.py comment for the Service Authorization Reference
    # citation.
    required = {
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:ListEvents",
        "bedrock-agentcore:ListSessions",  # Phase 8a recall
        "bedrock-agentcore:CreateMemory",
        "bedrock-agentcore:GetMemory",
        "bedrock-agentcore:ListMemories",
        "bedrock-agentcore:RetrieveMemoryRecords",  # Phase 7d recall (retained)
    }
    granted: set[str] = set()
    for pol in policies.values():
        for stmt in pol["Properties"]["PolicyDocument"]["Statement"]:
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            granted.update(actions)

    missing = required - granted
    assert not missing, f"AgentCore IAM actions missing from synth: {missing}"


# ----------------------------------------------------------------
# Attachments S3 bucket (#173) — epic #109 file attachments + vision
# ----------------------------------------------------------------


def _attachments_bucket(template: assertions.Template) -> dict:
    buckets = template.find_resources("AWS::S3::Bucket")
    attach = {key: val for key, val in buckets.items() if "AttachmentsBucket" in key}
    assert len(attach) == 1, f"expected exactly one AttachmentsBucket, got {list(attach)}"
    return next(iter(attach.values()))


def test_attachments_bucket_uses_sse_kms(dev_template):
    bucket = _attachments_bucket(dev_template)
    rules = bucket["Properties"]["BucketEncryption"]["ServerSideEncryptionConfiguration"]
    assert any(r["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "aws:kms" for r in rules), (
        "Attachments bucket must use SSE-KMS (aws/s3 AWS-managed key)"
    )


def test_attachments_bucket_blocks_public_access(dev_template):
    bucket = _attachments_bucket(dev_template)
    pab = bucket["Properties"]["PublicAccessBlockConfiguration"]
    assert pab["BlockPublicAcls"] is True
    assert pab["BlockPublicPolicy"] is True
    assert pab["IgnorePublicAcls"] is True
    assert pab["RestrictPublicBuckets"] is True


def test_attachments_bucket_lifecycle_expires_unreferenced(dev_template):
    bucket = _attachments_bucket(dev_template)
    rules = bucket["Properties"]["LifecycleConfiguration"]["Rules"]
    unreferenced = [
        r
        for r in rules
        if any(
            t.get("Key") == "unreferenced" and t.get("Value") == "1"
            for t in (r.get("TagFilters") or [])
        )
    ]
    assert len(unreferenced) == 1, (
        f"expected exactly one unreferenced lifecycle rule, got {len(unreferenced)}"
    )
    rule = unreferenced[0]
    assert rule["Status"] == "Enabled"
    assert rule["ExpirationInDays"] == 1


def test_attachments_bucket_cors_allows_cloudfront_in_prod(prod_template):
    bucket = _attachments_bucket(prod_template)
    cors_rules = bucket["Properties"]["CorsConfiguration"]["CorsRules"]
    assert len(cors_rules) == 1
    rule = cors_rules[0]
    assert "PUT" in rule["AllowedMethods"]
    assert "https://channel.warlordofmars.net" in rule["AllowedOrigins"]
    # Prod must NOT include localhost — keeps direct-PUT attack surface
    # tied to the production CloudFront origin.
    assert not any("localhost" in o for o in rule["AllowedOrigins"]), (
        "Prod CORS must not include localhost origins"
    )


def test_attachments_bucket_cors_allows_localhost_in_dev(dev_template):
    bucket = _attachments_bucket(dev_template)
    cors_rules = bucket["Properties"]["CorsConfiguration"]["CorsRules"]
    assert len(cors_rules) == 1
    rule = cors_rules[0]
    assert "PUT" in rule["AllowedMethods"]
    # Whole 5173-5179 port range — matches CORS_ORIGINS in tasks.py:432.
    for port in range(5173, 5180):
        assert f"http://localhost:{port}" in rule["AllowedOrigins"], (
            f"dev CORS missing localhost:{port}"
        )


def test_lambda_env_has_attachments_bucket_var(dev_template):
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert "STARTER_ATTACHMENTS_BUCKET" in env_vars
    # Per #173 design: we use the AWS-managed aws/s3 key, so there's no
    # CMK ARN to plumb. Issue body originally listed
    # STARTER_ATTACHMENTS_KMS_KEY_ARN; the AWS-managed-key choice made
    # it redundant.
    assert "STARTER_ATTACHMENTS_KMS_KEY_ARN" not in env_vars, (
        "AWS-managed key has no CMK ARN to plumb — see #173"
    )


# ----------------------------------------------------------------
# Content-Security-Policy header (#196)
# ----------------------------------------------------------------


def test_csp_header_includes_env_specific_custom_domain():
    """The pre-#196 CSP literal hardcoded ``https://channel.example.com``
    as a placeholder that was never substituted per-env. After #196 the
    helper builds the header per-env from ``custom_domain``."""

    from stacks.channel_stack import _build_csp_header  # noqa: E402

    header = _build_csp_header(
        custom_domain="channel-dev.warlordofmars.net",
        attachments_bucket_name="bucket-xyz",
        region="us-east-1",
    )
    assert "https://channel-dev.warlordofmars.net" in header
    # The old placeholder must be gone — its presence was the bug.
    assert "channel.example.com" not in header


def test_csp_header_includes_attachments_bucket_origins():
    """``connect-src`` must include the attachments bucket's S3 origins
    so browser PUT uploads pass CSP. Both us-east-1 legacy and regional
    virtual-host URL forms — the SDK can hand back either."""

    from stacks.channel_stack import _build_csp_header  # noqa: E402

    header = _build_csp_header(
        custom_domain="channel-dev.warlordofmars.net",
        attachments_bucket_name="bucket-xyz",
        region="us-east-1",
    )
    assert "https://bucket-xyz.s3.amazonaws.com" in header
    assert "https://bucket-xyz.s3.us-east-1.amazonaws.com" in header


def test_csp_header_preserves_existing_directives():
    """Regression — the refactor must not drop any directive (frame-ancestors,
    base-uri, etc.) that the pre-#196 literal carried."""

    from stacks.channel_stack import _build_csp_header  # noqa: E402

    header = _build_csp_header(
        custom_domain="channel-dev.warlordofmars.net",
        attachments_bucket_name="bucket-xyz",
        region="us-east-1",
    )
    for directive in (
        "default-src 'self'",
        "script-src 'self' https://www.googletagmanager.com",
        "img-src 'self' data:",
        "style-src 'self' 'unsafe-inline'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "report-uri /api/csp-report",
        "report-to default",
    ):
        assert directive in header, f"missing CSP directive: {directive!r}"


def _flatten_intrinsic(value):
    """Collapse a CloudFormation intrinsic (Fn::Join or string) into a
    single string by concatenating the literal parts. Tokens (``Ref`` /
    ``Fn::GetAtt`` etc.) become placeholders so the assertion can still
    grep for stable substrings around them."""

    if isinstance(value, str):
        return value
    if isinstance(value, dict) and "Fn::Join" in value:
        sep, parts = value["Fn::Join"]
        return sep.join(_flatten_intrinsic(p) for p in parts)
    if isinstance(value, dict):
        # Ref / Fn::GetAtt / etc. — represent as an opaque token marker.
        return "<TOKEN>"
    return str(value)


def test_synthed_dev_csp_resolves_dev_custom_domain(dev_template):
    """End-to-end CDK assertion: the synthed CloudFront response-headers
    policy carries a CSP that resolves the dev env's actual hostname."""

    policies = dev_template.find_resources("AWS::CloudFront::ResponseHeadersPolicy")
    assert len(policies) == 1
    policy = next(iter(policies.values()))
    custom_headers = policy["Properties"]["ResponseHeadersPolicyConfig"]["CustomHeadersConfig"][
        "Items"
    ]
    csp_items = [
        h for h in custom_headers if h["Header"].lower() == "content-security-policy-report-only"
    ]
    assert len(csp_items) == 1
    # The CSP value is a Fn::Join because the attachments bucket name is
    # a CDK token resolved at deploy time. Flatten the join to a string
    # for substring assertions.
    csp_value = _flatten_intrinsic(csp_items[0]["Value"])
    assert "https://channel-dev.warlordofmars.net" in csp_value
    assert "channel.example.com" not in csp_value
    # And the attachments bucket origins are wired in (token + suffixes).
    assert "<TOKEN>.s3.amazonaws.com" in csp_value
    assert "<TOKEN>.s3.us-east-1.amazonaws.com" in csp_value


def test_prod_stack_enables_web_search(prod_template):
    """STARTER_WEB_SEARCH_ENABLED = '1' in prod (kill switch, not rollout)."""
    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("STARTER_WEB_SEARCH_ENABLED") == "1"


def test_dev_stack_enables_web_search(dev_template):
    """STARTER_WEB_SEARCH_ENABLED = '1' in non-prod."""
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("STARTER_WEB_SEARCH_ENABLED") == "1"


def test_prod_stack_carries_mcp_env_vars(prod_template):
    """API Lambda must receive STARTER_MCP_REDIRECT_URI + STARTER_SPA_BASE_URL
    + STARTER_MCP_TOKEN_KMS_KEY_ID + STARTER_MCP_REGISTRY_ENABLED.

    The KMS key ID isn't a literal at synth-time (it's a CFN ref) so
    we just confirm presence, not value."""
    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("STARTER_MCP_REGISTRY_ENABLED") == "1"
    assert "STARTER_MCP_REDIRECT_URI" in env_vars
    assert "STARTER_SPA_BASE_URL" in env_vars
    assert "STARTER_MCP_TOKEN_KMS_KEY_ID" in env_vars


def test_prod_stack_mcp_token_kms_key_id_is_not_local_sentinel(prod_template):
    """Defense-in-depth — the local-dev passthrough sentinel must never
    leak to a deployed env. The CDK stack always wires a real KMS ARN
    via key_arn; this test confirms it's not the literal string 'local'."""
    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("STARTER_MCP_TOKEN_KMS_KEY_ID") != "local"


def test_dev_stack_mcp_token_kms_key_id_is_not_local_sentinel(dev_template):
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("STARTER_MCP_TOKEN_KMS_KEY_ID") != "local"


def test_stack_creates_mcp_token_kms_key(prod_template):
    """A dedicated CMK with rotation enabled exists for MCP tokens."""
    keys = prod_template.find_resources("AWS::KMS::Key")
    mcp_keys = {
        k: v for k, v in keys.items()
        if "MCP" in v["Properties"].get("Description", "")
    }
    assert len(mcp_keys) >= 1, (
        f"Expected an MCP token-encryption KMS key in stack; "
        f"found descriptions: {[v['Properties'].get('Description') for v in keys.values()]}"
    )
    one = next(iter(mcp_keys.values()))
    assert one["Properties"].get("EnableKeyRotation") is True


def test_prod_stack_sets_exa_api_key_param_path(prod_template):
    """STARTER_EXA_API_KEY_PARAM points at the per-env SSM path so the
    Lambda knows where to fetch the key."""
    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("STARTER_EXA_API_KEY_PARAM") == "/channel/prod/exa-api-key"


def test_dev_stack_sets_exa_api_key_param_path(dev_template):
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("STARTER_EXA_API_KEY_PARAM") == "/channel/dev/exa-api-key"


def test_api_role_has_ssm_read_on_exa_api_key(prod_template):
    """Walk ``AWS::IAM::Policy`` statements and assert that *the same*
    statement grants ``ssm:GetParameter`` on a Resource that includes
    the Exa parameter path. This is stronger than substring scanning
    the rendered template — the latter would pass even if the Exa
    grant disappeared, as long as some other SSM read existed for a
    different parameter."""

    resources = prod_template.to_json().get("Resources", {})
    policies = [r for r in resources.values() if r.get("Type") == "AWS::IAM::Policy"]
    assert policies, "Expected at least one AWS::IAM::Policy in the template"

    matched_statements = []
    for policy in policies:
        statements = policy["Properties"]["PolicyDocument"]["Statement"]
        for stmt in statements:
            actions = stmt.get("Action")
            if isinstance(actions, str):
                actions = [actions]
            if "ssm:GetParameter" not in (actions or []):
                continue
            resource = stmt.get("Resource")
            resources_list = resource if isinstance(resource, list) else [resource]
            for res in resources_list:
                flat = _flatten_intrinsic(res)
                if "/channel/prod/exa-api-key" in flat:
                    matched_statements.append(stmt)
                    break

    assert matched_statements, (
        "Expected an IAM statement granting ssm:GetParameter on a resource "
        "including '/channel/prod/exa-api-key'; found none."
    )


# ----------------------------------------------------------------
# Code-exec sandbox Lambda (#183)
# ----------------------------------------------------------------


def test_sandbox_lambda_exists_with_snapstart_and_python_3_13(dev_template):
    """``CodeExecLambda`` is created with Python 3.13 + SnapStart on
    published versions + reserved-concurrency 5 + 5-min timeout."""
    template = dev_template.to_json()
    sandbox = [
        r
        for r in template["Resources"].values()
        if r["Type"] == "AWS::Lambda::Function"
        and r["Properties"].get("Runtime") == "python3.13"
        and "code-exec" in str(r["Properties"].get("FunctionName", ""))
    ]
    assert len(sandbox) == 1, "expected exactly one CodeExecLambda"
    props = sandbox[0]["Properties"]
    assert props["Timeout"] == 300
    assert props["ReservedConcurrentExecutions"] == 5
    snap = props.get("SnapStart") or {}
    assert snap.get("ApplyOn") == "PublishedVersions"


def test_sandbox_lambda_role_has_no_data_access(dev_template):
    """Sandbox IAM role grants only ``AWSLambdaBasicExecutionRole`` —
    no DDB, S3, Bedrock, Secrets Manager, or SSM in any statement
    that targets the sandbox role."""
    template = dev_template.to_json()

    # Find the sandbox role by logical ID — CDK will name it
    # "CodeExecLambdaRole<hash>" so key.startswith("CodeExecLambdaRole")
    # uniquely identifies it.
    sandbox_role_entries = [
        (k, v)
        for k, v in template["Resources"].items()
        if v["Type"] == "AWS::IAM::Role" and k.startswith("CodeExecLambdaRole")
    ]
    assert len(sandbox_role_entries) == 1, (
        f"expected one CodeExecLambdaRole, found {len(sandbox_role_entries)}"
    )
    sandbox_role_logical_id, sandbox_role = sandbox_role_entries[0]

    # Verify the role has only the basic-execution managed policy.
    managed = sandbox_role["Properties"].get("ManagedPolicyArns", [])
    assert len(managed) == 1, (
        f"sandbox role must have exactly 1 managed policy, found {len(managed)}"
    )
    assert "AWSLambdaBasicExecutionRole" in _flatten_intrinsic(managed[0]), (
        "sandbox role's managed policy must be AWSLambdaBasicExecutionRole"
    )

    # Check no inline policies on the role grant data-access actions.
    # CDK attaches inline policies to the role via AWS::IAM::Policy resources.
    forbidden_prefixes = ("dynamodb:", "s3:", "bedrock:", "secretsmanager:", "ssm:")
    for resource in template["Resources"].values():
        if resource["Type"] != "AWS::IAM::Policy":
            continue
        roles = resource["Properties"].get("Roles", [])
        # Check if this policy references the sandbox role by Ref.
        refs_sandbox = any(
            isinstance(r, dict) and r.get("Ref") == sandbox_role_logical_id for r in roles
        )
        if not refs_sandbox:
            continue
        for stmt in resource["Properties"]["PolicyDocument"]["Statement"]:
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            for action in actions:
                action_str = str(action).lower()
                for prefix in forbidden_prefixes:
                    assert not action_str.startswith(prefix), (
                        f"sandbox role must not grant {action_str} (forbidden prefix {prefix})"
                    )


def test_api_lambda_can_invoke_sandbox(dev_template):
    """At least one IAM policy grants ``lambda:InvokeFunction``."""
    template = dev_template.to_json()
    invoke_grants = []
    for resource in template["Resources"].values():
        if resource["Type"] != "AWS::IAM::Policy":
            continue
        for stmt in resource["Properties"]["PolicyDocument"]["Statement"]:
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            if any("lambda:InvokeFunction" in str(a) for a in actions):
                invoke_grants.append(stmt)
    assert invoke_grants, "expected at least one lambda:InvokeFunction grant"


def test_api_lambda_has_code_exec_env_vars(dev_template):
    """API Lambda env vars include ``STARTER_CODE_EXEC_LAMBDA_ARN`` and
    ``STARTER_CODE_EXEC_ENABLED=1``."""
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert "STARTER_CODE_EXEC_LAMBDA_ARN" in env_vars
    assert env_vars.get("STARTER_CODE_EXEC_ENABLED") == "1"
