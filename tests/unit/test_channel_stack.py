# Copyright (c) 2026 John Carter. All rights reserved.
"""CDK template assertions for ChannelStack.

Runs as pure Python unit tests (no AWS calls). Guards env-specific
configuration that the agent-safe checklist + spec Risk #5 require:

- ``CHANNEL_ENABLE_DEBUG_ENDPOINTS`` must NEVER leak to the prod
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

import json
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk import assertions

# infra/app.py imports as ``from stacks.channel_stack import ChannelStack``,
# i.e. it expects ``infra/`` on sys.path. Replicate that for tests.
_INFRA = Path(__file__).resolve().parents[2] / "infra"
sys.path.insert(0, str(_INFRA))

from stacks.channel_stack import (  # noqa: E402
    API_LAMBDA_BUNDLING_STEPS,
    LAMBDA_ASSET_EXCLUDE,
    ChannelStack,
)


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
    assert "CHANNEL_ENABLE_DEBUG_ENDPOINTS" not in env_vars, (
        "Prod stack must NOT enable debug endpoints. See spec Risk #5."
    )


def test_dev_stack_sets_debug_endpoints_env_var(dev_template):
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_ENABLE_DEBUG_ENDPOINTS") == "1"


def test_prod_stack_does_not_set_bypass_google_auth_env_var(prod_template):
    """#179 — the Google-auth bypass MUST stay off in prod. It only
    activates when a request carries ``?test_email=``, but a
    misconfigured prod with the flag set still hands an admin JWT to
    anyone who can guess that query param. Same risk shape as
    ``CHANNEL_ENABLE_DEBUG_ENDPOINTS``."""

    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert "CHANNEL_BYPASS_GOOGLE_AUTH" not in env_vars, (
        "Prod stack must NOT enable the Google-auth bypass."
    )


def test_dev_stack_sets_bypass_google_auth_env_var(dev_template):
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_BYPASS_GOOGLE_AUTH") == "1"


def test_prod_stack_disables_clock_tool_env_var(prod_template):
    """#181 chassis policy P2 — the ``current_time`` smoke-test tool
    must NOT register in prod. The chassis registers the tool only
    when ``CHANNEL_CLOCK_TOOL_ENABLED=="1"``; prod sets it to ``"0"``
    explicitly so a future default-shift can't flip it on by accident.
    """

    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_CLOCK_TOOL_ENABLED") == "0"


def test_dev_stack_enables_clock_tool_env_var(dev_template):
    """Non-prod envs (dev / jc / etc.) get the chassis smoke-test tool
    so the end-to-end tool path stays exercised — see #181 strategy
    spec policy P2."""

    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_CLOCK_TOOL_ENABLED") == "1"


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


def test_api_lambda_role_grants_cloudwatch_read(dev_template):
    """#236 — the admin metrics endpoints read Channel-namespace EMF
    counters back via GetMetricData, so the action must be granted on
    the API Lambda role specifically (role-scoped check — a grant on
    some other role would not satisfy the endpoints). Resource must be
    ``"*"``: GetMetricData supports no resource types and no condition
    keys (``cloudwatch:namespace`` applies only to PutMetricData per
    the Service Authorization Reference), so the wildcard IS the
    minimal grant."""
    template = dev_template.to_json()

    api_role_ids = [
        k
        for k, v in template["Resources"].items()
        if v["Type"] == "AWS::IAM::Role" and k.startswith("ApiLambdaRole")
    ]
    assert len(api_role_ids) == 1, f"expected one ApiLambdaRole, found {len(api_role_ids)}"
    api_role_id = api_role_ids[0]

    found = False
    for resource in template["Resources"].values():
        if resource["Type"] != "AWS::IAM::Policy":
            continue
        roles = resource["Properties"].get("Roles", [])
        if not any(isinstance(r, dict) and r.get("Ref") == api_role_id for r in roles):
            continue
        for stmt in resource["Properties"]["PolicyDocument"]["Statement"]:
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            if "cloudwatch:GetMetricData" in actions:
                # CDK may emit Resource as a bare string or a
                # single-element list; accept both shapes.
                resource = stmt.get("Resource")
                resources = resource if isinstance(resource, list) else [resource]
                assert resources == ["*"], (
                    f"GetMetricData statement resource must be '*', got {resource}"
                )
                found = True
    assert found, "cloudwatch:GetMetricData missing from API Lambda role policies"


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


@pytest.mark.parametrize("template_name", ["dev_template", "prod_template"])
def test_attachments_bucket_cors_allows_desktop_app_origin(template_name, request):
    """#378 — the Electron desktop app loads the SPA from the ``app://``
    scheme (``desktop/main/window.js`` → ``loadURL("app://-/app")`` with the
    scheme registered ``standard: true`` in ``desktop/main/protocol.js``), so
    its renderer Origin is the fixed string ``app://-``. The presigned
    browser→S3 attachment PUT is cross-origin, so ``app://-`` must be in the
    bucket CORS allowlist in EVERY environment (added unconditionally) or the
    desktop upload is blocked while Chrome works.
    """
    template = request.getfixturevalue(template_name)
    bucket = _attachments_bucket(template)
    cors_rules = bucket["Properties"]["CorsConfiguration"]["CorsRules"]
    assert len(cors_rules) == 1
    rule = cors_rules[0]
    assert "PUT" in rule["AllowedMethods"]
    assert "app://-" in rule["AllowedOrigins"], (
        f"{template_name} CORS missing the desktop app origin 'app://-'"
    )


def test_lambda_env_has_attachments_bucket_var(dev_template):
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert "CHANNEL_ATTACHMENTS_BUCKET" in env_vars
    # Per #173 design: we use the AWS-managed aws/s3 key, so there's no
    # CMK ARN to plumb. Issue body originally listed
    # CHANNEL_ATTACHMENTS_KMS_KEY_ARN; the AWS-managed-key choice made
    # it redundant.
    assert "CHANNEL_ATTACHMENTS_KMS_KEY_ARN" not in env_vars, (
        "AWS-managed key has no CMK ARN to plumb — see #173"
    )


# ----------------------------------------------------------------
# Assets (#324, epic #321) — owner GSI + prefix-scoped IAM
# ----------------------------------------------------------------


def _single_table(template: assertions.Template) -> dict:
    tables = template.find_resources("AWS::DynamoDB::Table")
    assert len(tables) == 1, f"expected exactly one DynamoDB table, got {list(tables)}"
    return next(iter(tables.values()))


def test_table_has_asset_owner_index(dev_template):
    """AssetOwnerIndex must exist with the semantic ``owner_pk`` /
    ``owner_sk`` key attributes (not a GSI5PK slot) — the settled #321
    design keys the browse GSI off a single owner attribute so the
    workspace-tenancy migration is a one-value swap."""

    gsis = _single_table(dev_template)["Properties"]["GlobalSecondaryIndexes"]
    by_name = {g["IndexName"]: g for g in gsis}
    assert "AssetOwnerIndex" in by_name, f"AssetOwnerIndex missing; found {sorted(by_name)}"
    index = by_name["AssetOwnerIndex"]
    assert index["KeySchema"] == [
        {"AttributeName": "owner_pk", "KeyType": "HASH"},
        {"AttributeName": "owner_sk", "KeyType": "RANGE"},
    ]
    assert index["Projection"]["ProjectionType"] == "ALL"


def test_table_declares_owner_key_attributes_as_strings(dev_template):
    attrs = {
        a["AttributeName"]: a["AttributeType"]
        for a in _single_table(dev_template)["Properties"]["AttributeDefinitions"]
    }
    assert attrs.get("owner_pk") == "S"
    assert attrs.get("owner_sk") == "S"


# ----------------------------------------------------------------
# Refresh tokens (#290, epic #241) — per-user lookup GSI
# ----------------------------------------------------------------


def test_table_has_refresh_by_user_index(dev_template):
    """RefreshByUserIndex must exist on the GSI5PK/GSI5SK slot (#290).

    GSI3 is ChatByIdIndex and GSI4 is UserEmailIndex; AssetOwnerIndex
    deliberately uses semantic ``owner_pk``/``owner_sk`` names rather
    than a numbered slot (#324), which left GSI5 free. Asserting the
    exact key schema here is what stops a future index from silently
    reusing the slot and colliding with refresh rows.
    """

    gsis = _single_table(dev_template)["Properties"]["GlobalSecondaryIndexes"]
    by_name = {g["IndexName"]: g for g in gsis}
    assert "RefreshByUserIndex" in by_name, f"RefreshByUserIndex missing; found {sorted(by_name)}"
    index = by_name["RefreshByUserIndex"]
    assert index["KeySchema"] == [
        {"AttributeName": "GSI5PK", "KeyType": "HASH"},
        {"AttributeName": "GSI5SK", "KeyType": "RANGE"},
    ]
    # Projection ALL: #293's session list reads device_id / issued_at /
    # last_used_at straight off the index, and the revoke helpers need
    # token_hash + revoked without a base-table round trip per row.
    assert index["Projection"]["ProjectionType"] == "ALL"


def test_table_declares_gsi5_key_attributes_as_strings(dev_template):
    attrs = {
        a["AttributeName"]: a["AttributeType"]
        for a in _single_table(dev_template)["Properties"]["AttributeDefinitions"]
    }
    assert attrs.get("GSI5PK") == "S"
    assert attrs.get("GSI5SK") == "S"


def test_table_ttl_attribute_covers_refresh_row_expiry(dev_template):
    """Refresh rows self-prune via the table-wide ``ttl`` attribute (#290).

    ``_refresh_item`` writes ``ttl`` as the absolute expiry in integer
    Unix seconds; that only prunes anything if the table's TTL
    specification names exactly that attribute.
    """

    spec = _single_table(dev_template)["Properties"]["TimeToLiveSpecification"]
    assert spec["AttributeName"] == "ttl"
    assert spec["Enabled"] is True


def _actions_touching_resource_substring(template: assertions.Template, needle: str) -> set[str]:
    """Collect every IAM action from statements whose Resource JSON
    mentions ``needle``. CDK renders prefix-scoped bucket grants as
    ``Fn::Join`` fragments, so substring matching on the serialized
    resource is the robust way to find them."""

    actions: set[str] = set()
    for pol in template.find_resources("AWS::IAM::Policy").values():
        for stmt in pol["Properties"]["PolicyDocument"]["Statement"]:
            if needle not in json.dumps(stmt.get("Resource", [])):
                continue
            stmt_actions = stmt.get("Action", [])
            if isinstance(stmt_actions, str):
                stmt_actions = [stmt_actions]
            actions.update(stmt_actions)
    return actions


def test_api_role_can_put_read_delete_assets_prefix(dev_template):
    """#324 — asset payloads live in the attachments bucket under
    ``assets/chat/{chat_id}/{asset_id}``; the API role needs
    put/read/delete scoped to that prefix."""

    actions = _actions_touching_resource_substring(dev_template, "assets/chat/*")
    assert any(a.startswith("s3:PutObject") for a in actions), actions
    assert any(a.startswith("s3:GetObject") for a in actions), actions
    assert any(a.startswith("s3:DeleteObject") for a in actions), actions


def test_assets_prefix_has_no_dedicated_delete_tagging_statement(dev_template):
    """Assets never carry the ``unreferenced=1`` upload lifecycle tag, so
    the dedicated ``s3:DeleteObjectTagging`` statement that presigned
    uploads need on ``attachments/user/*`` must not be duplicated for
    the assets prefix. (``s3:PutObjectTagging`` alone is not asserted
    on — CDK's ``grant_put`` bundles it into its standard write action
    set for every prefix.)"""

    actions = _actions_touching_resource_substring(dev_template, "assets/chat/*")
    assert "s3:DeleteObjectTagging" not in actions, actions


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
    """CHANNEL_WEB_SEARCH_ENABLED = '1' in prod (kill switch, not rollout)."""
    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_WEB_SEARCH_ENABLED") == "1"


def test_dev_stack_enables_web_search(dev_template):
    """CHANNEL_WEB_SEARCH_ENABLED = '1' in non-prod."""
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_WEB_SEARCH_ENABLED") == "1"


def test_prod_stack_enables_image_gen(prod_template):
    """CHANNEL_IMAGE_GEN_ENABLED = '1' in prod (#279 kill switch, on default);
    CHANNEL_IMAGE_GEN_REGION pins the cross-region image call to us-west-2."""
    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_IMAGE_GEN_ENABLED") == "1"
    assert env_vars.get("CHANNEL_IMAGE_GEN_REGION") == "us-west-2"


def test_dev_stack_enables_image_gen(dev_template):
    """CHANNEL_IMAGE_GEN_ENABLED = '1' in non-prod; CHANNEL_IMAGE_GEN_REGION
    pins the cross-region image call to us-west-2."""
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_IMAGE_GEN_ENABLED") == "1"
    assert env_vars.get("CHANNEL_IMAGE_GEN_REGION") == "us-west-2"


def test_lambda_role_grants_stability_image_invoke_model(dev_template):
    """#279 — the ``generate_image`` tool calls ``bedrock:InvokeModel`` on the
    Stability text-to-image generators in **us-west-2** (a cross-region call —
    these models are ACTIVE only in us-west-2, absent from the stack's
    us-east-1 region), so all three region-pinned foundation-model ARNs must
    appear in the API Lambda role's IAM policies. All three (Core / Ultra /
    SD3.5 Large) are granted so a ``CHANNEL_IMAGE_GEN_MODEL`` switch needs no
    redeploy. Without the grant every generation fails with
    AccessDeniedException. Scan every IAM::Policy resource because CDK
    distributes statements across multiple Policy resources."""
    stability_arns = {
        "arn:aws:bedrock:us-west-2::foundation-model/stability.stable-image-core-v1:1",
        "arn:aws:bedrock:us-west-2::foundation-model/stability.stable-image-ultra-v1:1",
        "arn:aws:bedrock:us-west-2::foundation-model/stability.sd3-5-large-v1:0",
    }
    policies = dev_template.find_resources("AWS::IAM::Policy")
    granted_resources: set[str] = set()
    for pol in policies.values():
        for stmt in pol["Properties"]["PolicyDocument"]["Statement"]:
            resources = stmt.get("Resource", [])
            if isinstance(resources, str):
                resources = [resources]
            granted_resources.update(r for r in resources if isinstance(r, str))
    missing = stability_arns - granted_resources
    assert not missing, (
        f"Stability image-model InvokeModel ARN(s) missing from synth: "
        f"{sorted(missing)}; granted {sorted(granted_resources)}"
    )


def test_prod_stack_carries_mcp_env_vars(prod_template):
    """API Lambda must receive CHANNEL_MCP_REDIRECT_URI + CHANNEL_SPA_BASE_URL
    + CHANNEL_MCP_TOKEN_KMS_KEY_ID + CHANNEL_MCP_REGISTRY_ENABLED.

    The KMS key ID isn't a literal at synth-time (it's a CFN ref) so
    we just confirm presence, not value."""
    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_MCP_REGISTRY_ENABLED") == "1"
    assert "CHANNEL_MCP_REDIRECT_URI" in env_vars
    assert "CHANNEL_SPA_BASE_URL" in env_vars
    assert "CHANNEL_MCP_TOKEN_KMS_KEY_ID" in env_vars


@pytest.mark.parametrize("env_fixture", ["dev_template", "prod_template"])
def test_mcp_max_tools_per_server_is_pinned_to_35(env_fixture, request):
    """#561 — the per-server MCP tool budget is pinned in the template.

    The code default (``_DEFAULT_MCP_MAX_TOOLS_PER_SERVER = 24``) is
    smaller than the GitHub MCP server's 28 read-only tools, so under the
    #560 read-preferring selection every slot went to a read and all 19
    write tools were dropped — ``api_issue_write`` included. 35 fits the
    whole read surface (``dropped_read_only`` → 0) plus 7 write slots.

    Pinned here rather than left to the code default for the same reason
    as the other deployed knobs (``#181`` clock tool, ``#279`` image gen,
    ``#111`` route dimension): the deployed value stays auditable in the
    template, and a console edit to the Lambda env map would be clobbered
    by the next ``cdk deploy`` — so this IS the flip point.
    """
    api_fn = _api_function(request.getfixturevalue(env_fixture))
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_MCP_MAX_TOOLS_PER_SERVER") == "35"


def test_prod_stack_mcp_token_kms_key_id_is_not_local_sentinel(prod_template):
    """Defense-in-depth — the local-dev passthrough sentinel must never
    leak to a deployed env. The CDK stack always wires a real KMS ARN
    via key_arn; this test confirms it's not the literal string 'local'."""
    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_MCP_TOKEN_KMS_KEY_ID") != "local"


def test_dev_stack_mcp_token_kms_key_id_is_not_local_sentinel(dev_template):
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_MCP_TOKEN_KMS_KEY_ID") != "local"


def test_stack_creates_mcp_token_kms_key(prod_template):
    """A dedicated CMK with rotation enabled exists for MCP tokens."""
    keys = prod_template.find_resources("AWS::KMS::Key")
    mcp_keys = {k: v for k, v in keys.items() if "MCP" in v["Properties"].get("Description", "")}
    assert len(mcp_keys) >= 1, (
        f"Expected an MCP token-encryption KMS key in stack; "
        f"found descriptions: {[v['Properties'].get('Description') for v in keys.values()]}"
    )
    one = next(iter(mcp_keys.values()))
    assert one["Properties"].get("EnableKeyRotation") is True


def test_prod_stack_sets_exa_api_key_param_path(prod_template):
    """CHANNEL_EXA_API_KEY_PARAM points at the per-env SSM path so the
    Lambda knows where to fetch the key."""
    api_fn = _api_function(prod_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_EXA_API_KEY_PARAM") == "/channel/prod/exa-api-key"


def test_dev_stack_sets_exa_api_key_param_path(dev_template):
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_EXA_API_KEY_PARAM") == "/channel/dev/exa-api-key"


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

    # Verify the role has exactly the two expected managed policies:
    # basic execution (CloudWatch Logs) plus VPC access (ENI lifecycle
    # for the #249 isolated-VPC attachment — attached explicitly in
    # the stack because CDK only auto-attaches it on roles it creates;
    # grants ec2:*NetworkInterface* only, no data access).
    managed = sandbox_role["Properties"].get("ManagedPolicyArns", [])
    assert len(managed) == 2, (
        f"sandbox role must have exactly 2 managed policies, found {len(managed)}"
    )
    flattened = [_flatten_intrinsic(m) for m in managed]
    assert any("AWSLambdaBasicExecutionRole" in f for f in flattened), (
        "sandbox role must carry AWSLambdaBasicExecutionRole"
    )
    assert any("AWSLambdaVPCAccessExecutionRole" in f for f in flattened), (
        "sandbox role must carry AWSLambdaVPCAccessExecutionRole (ENI lifecycle)"
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
    """API Lambda env vars include ``CHANNEL_CODE_EXEC_LAMBDA_ARN`` and
    ``CHANNEL_CODE_EXEC_ENABLED=1``."""
    api_fn = _api_function(dev_template)
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert "CHANNEL_CODE_EXEC_LAMBDA_ARN" in env_vars
    assert env_vars.get("CHANNEL_CODE_EXEC_ENABLED") == "1"


# ----------------------------------------------------------------
# Code-exec sandbox network isolation (#249)
# ----------------------------------------------------------------


def _sandbox_function(template_json: dict) -> dict:
    """Return the CodeExecLambda function resource from a template dict.

    Located by logical-ID prefix (consistent with the sandbox-role
    lookup in ``test_sandbox_lambda_role_has_no_data_access``) — logical
    IDs are always plain strings, while property values like
    ``FunctionName`` can be CloudFormation intrinsics."""
    sandbox = [
        r
        for k, r in template_json["Resources"].items()
        if r["Type"] == "AWS::Lambda::Function" and k.startswith("CodeExecLambda")
    ]
    assert len(sandbox) == 1, "expected exactly one CodeExecLambda"
    return sandbox[0]


def test_sandbox_lambda_attached_to_vpc_with_zero_egress_sg(dev_template):
    """#249 — the code-exec Lambda must carry a ``VpcConfig`` wiring it
    into the isolated sandbox VPC, with the zero-egress security group
    as its ONLY security group."""
    template = dev_template.to_json()
    vpc_config = _sandbox_function(template)["Properties"].get("VpcConfig")
    assert vpc_config, "CodeExecLambda must have a VpcConfig (#249 egress isolation)"
    assert len(vpc_config.get("SubnetIds", [])) >= 2, (
        "CodeExecLambda must span at least two isolated subnets"
    )
    assert len(vpc_config.get("SecurityGroupIds", [])) == 1, (
        "CodeExecLambda must use exactly one (zero-egress) security group"
    )


def test_sandbox_vpc_subnets_are_isolated_with_no_internet_path(dev_template):
    """#249 — the sandbox VPC's subnets are PRIVATE_ISOLATED, and the
    template contains no internet path at all: no internet gateway, no
    NAT gateway, no elastic IP, and no route entries (isolated subnets
    carry only the implicit ``local`` route)."""
    template = dev_template.to_json()
    # The internet-path ban is intentionally template-global: today the
    # sandbox VPC is the ONLY VPC in the stack, so any of these types
    # appearing anywhere means an egress path exists. If a second,
    # legitimately-internet-connected VPC ever lands, this must be
    # re-scoped deliberately (a conscious security decision), not
    # loosened in passing.
    resource_types = {r["Type"] for r in template["Resources"].values()}
    for forbidden in (
        "AWS::EC2::InternetGateway",
        "AWS::EC2::VPCGatewayAttachment",
        "AWS::EC2::NatGateway",
        "AWS::EC2::EIP",
        "AWS::EC2::Route",
    ):
        assert forbidden not in resource_types, (
            f"{forbidden} must not exist — the sandbox VPC is PRIVATE_ISOLATED "
            "(no IGW, no NAT, no routes). See #249."
        )

    # Scope the subnet assertions to the sandbox VPC's own subnets
    # (logical-ID prefix ``CodeExecVpc``) so an unrelated future VPC
    # can't produce false failures here.
    subnets = [
        r
        for k, r in template["Resources"].items()
        if r["Type"] == "AWS::EC2::Subnet" and k.startswith("CodeExecVpc")
    ]
    assert len(subnets) == 2, "expected the sandbox VPC's two isolated subnets"
    for subnet in subnets:
        tags = {t["Key"]: t["Value"] for t in subnet["Properties"].get("Tags", [])}
        assert tags.get("aws-cdk:subnet-type") == "Isolated", (
            "every sandbox VPC subnet must be PRIVATE_ISOLATED (#249)"
        )
        assert subnet["Properties"].get("MapPublicIpOnLaunch") is False


def test_sandbox_security_group_has_no_egress_rules(dev_template):
    """#249 — the sandbox security group is created with
    ``allow_all_outbound=False`` and NO egress rules added. CDK
    synthesizes that as the canonical no-op ICMP 'Disallow all traffic'
    placeholder (CloudFormation restores allow-all when the egress list
    is empty, so CDK pins an unmatchable rule instead). Assert the
    placeholder is the ONLY egress entry and nothing adds a real rule."""
    template = dev_template.to_json()
    # Select the sandbox SG by logical-ID prefix so an unrelated future
    # SG can't produce false failures here.
    sg_entries = [
        (k, r)
        for k, r in template["Resources"].items()
        if r["Type"] == "AWS::EC2::SecurityGroup" and k.startswith("CodeExecSandboxSg")
    ]
    assert len(sg_entries) == 1, "expected exactly one CodeExecSandboxSg"
    sg_logical_id, sg = sg_entries[0]
    egress = sg["Properties"].get("SecurityGroupEgress", [])
    assert len(egress) == 1, "sandbox SG must have exactly the no-op egress entry"
    rule = egress[0]
    assert rule["CidrIp"] == "255.255.255.255/32"
    assert rule["IpProtocol"] == "icmp"
    # No ingress either — nothing initiates connections INTO the sandbox.
    assert not sg["Properties"].get("SecurityGroupIngress")
    # No standalone rule resources may widen THIS SG after the fact —
    # check any SecurityGroupEgress/Ingress resource that references the
    # sandbox SG's logical id (Ref or Fn::GetAtt both serialize it).
    for k, r in template["Resources"].items():
        if r["Type"] not in ("AWS::EC2::SecurityGroupEgress", "AWS::EC2::SecurityGroupIngress"):
            continue
        assert sg_logical_id not in json.dumps(r), (
            f"standalone rule resource {k} must not target the sandbox SG (#249)"
        )


def _docs_rewrite_function_code(template: assertions.Template) -> str:
    """Return the inline JS of the ``/docs`` CloudFront rewrite function."""
    funcs = template.find_resources("AWS::CloudFront::Function")
    for fn in funcs.values():
        code = fn["Properties"].get("FunctionCode", "")
        if "/docs" in code:
            return code
    raise AssertionError("docs CloudFront rewrite function not found in template")


def test_docs_root_rewrites_to_index_not_redirect_to_missing_path(dev_template):
    """#230: ``/docs`` and ``/docs/`` must REWRITE to ``/docs/index.html``
    (which VitePress builds from ``docs-site/index.md``), not 302-redirect
    to ``/docs/getting-started/`` — VitePress never produces a
    ``getting-started/index.html``, so the old redirect fell through to the
    S3 404 → SPA-index fallback and served the marketing app at a docs URL.
    """
    code = _docs_rewrite_function_code(dev_template)
    # The landing page is served by rewriting to the real built file.
    assert "/docs/index.html" in code
    # The broken redirect target must be gone.
    assert "/docs/getting-started/" not in code


# ---------------------------------------------------------------------------
# Lambda asset fingerprint excludes (#316)
# ---------------------------------------------------------------------------


def test_lambda_asset_root_is_pinned_to_repo_root():
    """#316 — relative ``from_asset`` paths resolve against the *process
    cwd* (jsii's node child inherits it), so the old ``".."`` literal
    meant "repo root" only under the cdk CLI (cwd = ``infra/``). Under
    pytest (cwd = repo root) it resolved to the repo's PARENT directory,
    fingerprinting every sibling project. The root must be pinned to the
    repo root via the stack module's own location, cwd-independent.
    """
    from stacks.channel_stack import _ASSET_ROOT

    assert Path(_ASSET_ROOT) == Path(__file__).resolve().parents[2]
    assert Path(_ASSET_ROOT).is_absolute()


def test_lambda_asset_exclude_covers_volatile_agent_paths():
    """#316 — both ``from_asset("..")`` calls must exclude the volatile
    paths that sibling ``.claude/worktrees/`` sessions churn during
    concurrent ``inv pre-push`` runs. Without these, CDK's source
    fingerprint walks the entire repo tree and races sibling-worktree
    cache deletion (ENOENT mid-walk). The load-bearing patterns are the
    directory names themselves — CDK checks a directory against the
    ignore list BEFORE recursing, so a bare name prunes the subtree.
    """
    for required in (
        ".claude",  # the actual race trigger (agent worktrees + tmp state)
        ".git",
        "**/__pycache__",
        "**/.mypy_cache",
        "**/.pytest_cache",
        "**/.ruff_cache",
        "**/.venv",
        "**/node_modules",
        "**/coverage",
        "htmlcov",
        "ui/dist",
        "desktop/dist-main",
        "desktop/dist-renderer",
        "desktop/release",
        "docs-site/.vitepress/dist",
        "**/cdk.out",
    ):
        assert required in LAMBDA_ASSET_EXCLUDE, f"missing exclude pattern: {required}"


def test_api_bundling_uv_export_omits_the_project_itself():
    """#319 — the ``uv export`` bundling step must carry
    ``--no-emit-project``. Without it the export contains the project as
    an editable requirement (``-e .``), pip builds the project inside the
    bundling container, and hatch-vcs tries to resolve the version from
    ``.git`` — which is a pointer FILE in a git worktree, referencing a
    gitdir outside the Docker bind mount. Synth/deploy from a
    ``.claude/worktrees/`` worktree dies on exactly that. The dev and
    infra (CDK) dependency groups must stay excluded too — neither
    belongs in the Lambda zip.
    """
    export_steps = [s for s in API_LAMBDA_BUNDLING_STEPS if "uv export" in s]
    assert len(export_steps) == 1, "expected exactly one uv export step"
    export = export_steps[0]
    assert "--no-emit-project" in export, (
        "uv export must not emit the project — hatch-vcs cannot resolve a "
        "version inside the Docker bundling mount of a git worktree (#319)"
    )
    assert "--no-group dev" in export
    assert "--no-group infra" in export


def test_api_bundling_ships_first_party_code_and_entrypoint():
    """#319 — dropping the project from the export is only safe while the
    bundle ships first-party code via the explicit ``src/channel`` copy,
    installs the exported third-party deps into the asset output, and
    copies + chmods the AWSLWA entrypoint. Guard those steps so a future
    command rewrite can't silently remove what makes ``--no-emit-project``
    sound.
    """
    joined = " && ".join(API_LAMBDA_BUNDLING_STEPS)
    assert "pip install -r /tmp/requirements.txt -t /asset-output" in joined
    assert "cp -r src/channel /asset-output/channel" in joined
    assert "cp run.sh /asset-output/run.sh" in joined
    assert "chmod +x /asset-output/run.sh" in joined


def test_lambda_asset_exclude_never_matches_bundling_inputs():
    """#316 — the Docker bundling commands stage from the asset source,
    so the exclude list must never shadow an input either bundler needs:
    ``pyproject.toml`` + ``uv.lock`` (``uv export``), ``run.sh`` (AWSLWA
    entrypoint), and the ``src/channel`` tree (both Lambdas, including
    ``src/channel/sandbox/requirements.txt``). Guard the list against a
    future pattern that would prune them.
    """
    protected = ("src", "pyproject.toml", "uv.lock", "run.sh")
    for pattern in LAMBDA_ASSET_EXCLUDE:
        stripped = pattern.removeprefix("**/")
        for path in protected:
            assert stripped != path and not stripped.startswith(f"{path}/"), (
                f"exclude pattern {pattern!r} would shadow bundling input {path!r}"
            )
        # No pattern may reach into the shipped source tree at any depth.
        assert "channel/" not in pattern and not pattern.endswith("requirements.txt"), (
            f"exclude pattern {pattern!r} touches the src/channel bundling inputs"
        )


def test_lambda_asset_exclude_shields_fingerprint_from_junk_churn(tmp_path):
    """#316 regression test — exercise CDK's REAL fingerprint machinery
    (the same ``minimatch``-based walk ``from_asset`` uses) against the
    actual ``LAMBDA_ASSET_EXCLUDE`` list, on a throwaway mini-tree so the
    live repo is never mutated (parallel sessions share it):

    1. junk churn under ``.claude/worktrees/`` and cache dirs must NOT
       move the hash (the exact bug: junk perturbed it AND raced ENOENT);
    2. a change to any bundling input (``src/channel/**``,
       ``pyproject.toml``, ``uv.lock``, ``run.sh``,
       ``src/channel/sandbox/requirements.txt``) MUST move the hash —
       proving no exclude pattern shadows what the bundlers need.
    """
    root = tmp_path / "repo"
    sandbox = root / "src" / "channel" / "sandbox"
    sandbox.mkdir(parents=True)
    (root / "src" / "channel" / "models.py").write_text("x = 1\n")
    (sandbox / "requirements.txt").write_text("numpy==2.2.6\n")
    (root / "pyproject.toml").write_text("[project]\n")
    (root / "uv.lock").write_text("lock\n")
    (root / "run.sh").write_text("#!/bin/bash\n")

    def fp() -> str:
        return cdk.FileSystem.fingerprint(str(root), exclude=LAMBDA_ASSET_EXCLUDE)

    baseline = fp()

    # 1. Junk churn at every volatile depth must be invisible to the hash.
    junk_worktree = root / ".claude" / "worktrees" / "flaketest"
    junk_worktree.mkdir(parents=True)
    (junk_worktree / "dummy.txt").write_text("junk\n")
    (root / "src" / "channel" / "__pycache__").mkdir()
    (root / "src" / "channel" / "__pycache__" / "models.cpython-312.pyc").write_text("pyc")
    (root / ".mypy_cache").mkdir()
    (root / ".mypy_cache" / "state.json").write_text("{}")
    assert fp() == baseline, "excluded junk paths must not perturb the asset fingerprint"

    # 2. Every bundling input must still be fingerprinted.
    for bundling_input in (
        root / "src" / "channel" / "models.py",
        sandbox / "requirements.txt",
        root / "pyproject.toml",
        root / "uv.lock",
        root / "run.sh",
    ):
        before = fp()
        bundling_input.write_text(bundling_input.read_text() + "# changed\n")
        assert fp() != before, f"{bundling_input.name} must still move the fingerprint"


# ----------------------------------------------------------------
# Observability v2 (#111) — alarms must reference metrics we emit
# ----------------------------------------------------------------


@pytest.mark.parametrize("env_fixture", ["dev_template", "prod_template"])
def test_request_route_dimension_flag_is_set_explicitly(env_fixture, request):
    """The per-``Route`` EMF dimension set is the cost lever on #111, so
    the deployed value is pinned in the template rather than left to a code
    default — same discipline as the other kill switches (``#181`` clock
    tool, ``#279`` image gen). A console edit to the Lambda env map would be
    clobbered by the next ``cdk deploy``, so this IS the flip point."""
    api_fn = _api_function(request.getfixturevalue(env_fixture))
    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("CHANNEL_REQUEST_ROUTE_DIMENSION_ENABLED") == "1"


def _alarms(template: assertions.Template) -> dict[str, dict]:
    """Every ``AWS::CloudWatch::Alarm`` keyed by its ``AlarmName``."""
    return {
        res["Properties"]["AlarmName"]: res["Properties"]
        for res in template.find_resources("AWS::CloudWatch::Alarm").values()
    }


def _channel_metric_names(alarm: dict) -> set[str]:
    """``Channel``-namespace metric names an alarm depends on.

    Covers both shapes: a plain ``MetricName`` alarm and a metric-math
    alarm, whose component metrics hang off ``Metrics[].MetricStat``.
    """
    names: set[str] = set()
    if alarm.get("Namespace") == "Channel":
        names.add(alarm["MetricName"])
    for entry in alarm.get("Metrics", []):
        stat = entry.get("MetricStat")
        if stat and stat["Metric"].get("Namespace") == "Channel":
            names.add(stat["Metric"]["MetricName"])
    return names


def test_every_channel_namespace_alarm_references_an_emitted_metric(dev_template):
    """The regression this issue exists to prevent.

    Three alarms used to watch metric names no code path ever emitted
    (``ToolErrors`` / ``StorageLatencyMs`` / ``TokenValidationFailures``);
    with ``treat_missing_data=NOT_BREACHING`` they sat permanently green,
    advertising coverage that did not exist. Pin every Channel-namespace
    alarm to a name ``channel.metrics`` actually publishes so a renamed or
    dropped counter fails here instead of silently going dark in prod.
    """
    import ast

    def _live_string_literals(path: Path) -> set[str]:
        """Every string constant in ``path`` EXCEPT docstrings.

        Excluding docstrings is the load-bearing part. Metric names reach
        ``emit_metric`` three different ways — a literal argument, a ternary
        bound to a local, an ``_emit_batch`` tuple — so matching call shapes
        misses some. Matching every literal catches all three, but would also
        accept a name that only ever appeared in a usage example: an earlier
        draft of this test passed while `StorageLatencyMs` was documented and
        unemitted, which is exactly the dead-metric shape being guarded here.
        """
        tree = ast.parse(path.read_text())
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                first = node.body[0] if node.body else None
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    docstrings.add(id(first.value))
        return {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        }

    src_root = Path(__file__).resolve().parents[2] / "src" / "channel"
    emitted = _live_string_literals(src_root / "metrics.py") | _live_string_literals(
        src_root / "api" / "csp.py"
    )
    # Self-check — without this the subset assertion below could pass
    # vacuously if the extractor ever over-collected.
    assert not ({"ToolErrors", "StorageLatencyMs", "TokenValidationFailures"} & emitted)

    referenced: set[str] = set()
    for alarm in _alarms(dev_template).values():
        referenced |= _channel_metric_names(alarm)

    assert referenced, "expected at least one Channel-namespace alarm"
    assert referenced <= emitted, f"alarms watch unemitted metrics: {referenced - emitted}"


def test_request_and_bedrock_sli_alarms_exist(dev_template):
    """#111's alarmable SLIs, including the burn-rate pair whose fast
    window satisfies the issue's "fires within 1h of a sustained 5xx
    spike" definition of done."""
    names = set(_alarms(dev_template))
    assert {
        "Channel-dev-ApiRequestErrorRate",
        "Channel-dev-ApiRequestLatencyHigh",
        "Channel-dev-ApiRequestFastBurn",
        "Channel-dev-ApiRequestSlowBurn",
        "Channel-dev-BedrockErrorRate",
        "Channel-dev-BedrockThrottles",
    } <= names


def test_request_burn_rate_alarms_use_the_documented_windows(dev_template):
    """Fast burn = 1h at 5x budget, slow burn = 6h at 2x budget."""
    alarms = _alarms(dev_template)
    fast = alarms["Channel-dev-ApiRequestFastBurn"]
    slow = alarms["Channel-dev-ApiRequestSlowBurn"]
    assert fast["Threshold"] == 5.0
    assert slow["Threshold"] == 2.0
    assert all(e["MetricStat"]["Period"] == 3600 for e in fast["Metrics"] if "MetricStat" in e)
    assert all(e["MetricStat"]["Period"] == 21600 for e in slow["Metrics"] if "MetricStat" in e)


def test_channel_alarms_select_only_the_aggregate_dimension_set(dev_template):
    """Alarms must NOT select the per-``Route`` breakdown series (#111).

    CloudWatch matches a metric on an exact dimension set, so an alarm
    carrying a ``Route`` dimension would watch one route instead of the
    service. The aggregate ``{Environment}`` set is the alarmable one.
    """
    for name, alarm in _alarms(dev_template).items():
        dimension_sets = []
        if alarm.get("Namespace") == "Channel":
            dimension_sets.append(alarm.get("Dimensions", []))
        for entry in alarm.get("Metrics", []):
            stat = entry.get("MetricStat")
            if stat and stat["Metric"].get("Namespace") == "Channel":
                dimension_sets.append(stat["Metric"].get("Dimensions", []))
        for dims in dimension_sets:
            assert [d["Name"] for d in dims] == ["Environment"], name
