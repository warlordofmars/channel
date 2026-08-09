# Copyright (c) 2026 John Carter. All rights reserved.
"""
Channel CDK Stack — defines all AWS infrastructure.

Resources:
  - DynamoDB table (single-table design) with GSIs and TTL
  - Lambda function for the API (FastAPI + uvicorn behind AWSLWA)
  - Function URL for the Lambda (auth=NONE, TLS enforced)
  - IAM role scoped to DynamoDB table and SSM access
  - SSM Parameters for secrets
  - S3 bucket + CloudFront distribution for the React management UI
  - GitHub Actions OIDC deploy role (one per environment)

Multi-environment usage:
  cdk deploy ChannelStack         -c env=prod   # production
  cdk deploy ChannelStack-dev     -c env=dev    # development
  cdk deploy ChannelStack-staging -c env=staging
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import aws_cdk as cdk
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as route53_targets
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_sns as sns
from aws_cdk import aws_ssm as ssm
from aws_cdk import aws_wafv2 as wafv2
from cdk_nag import NagPackSuppression, NagSuppressions
from constructs import Construct

GITHUB_REPO = "warlordofmars/channel"


HOSTED_ZONE_NAME = "warlordofmars.net"

# Root for both Lambda ``Code.from_asset`` calls (#316). Relative asset
# paths resolve against the *process cwd* (verified empirically — jsii's
# node child inherits the Python cwd), NOT against ``infra/``. The old
# ``".."`` literal therefore meant "repo root" only when run via the cdk
# CLI (cwd = ``infra/``); under ``inv pre-push`` (pytest cwd = repo
# root) it resolved to the PARENT of the repo, fingerprinting every
# sibling project. Pinning to this module's location makes the asset
# root the repo/worktree root regardless of cwd.
_ASSET_ROOT = str(Path(__file__).resolve().parents[2])

# Paths excluded from both Lambda asset fingerprint walks (#316),
# relative to ``_ASSET_ROOT``. Without these, every synth (including the
# ``tests/unit/test_channel_stack.py`` template assertions run by
# ``inv pre-push``) fingerprints the ENTIRE repo tree — including
# ``.claude/worktrees/``, where sibling agent sessions churn caches
# concurrently. That churn deletes files between CDK's directory
# listing and its stat of them, killing the walk with ENOENT.
#
# Both assets are Docker-bundled with the default
# ``AssetHashType.SOURCE``, so these excludes only shape the source
# fingerprint (and staging); the bundling commands define the deployed
# zip contents. Keep this list conservative — junk, caches, and build
# outputs only. The bundling inputs (``pyproject.toml``, ``uv.lock``,
# ``run.sh``, ``src/channel/**`` including
# ``src/channel/sandbox/requirements.txt``) must never match.
#
# Pattern semantics: CDK matches with minimatch (``dot: true``,
# ``IgnoreMode.GLOB``) against paths relative to the asset root, and
# checks directories BEFORE recursing — so a bare directory name
# (e.g. ``".claude"``) prunes the whole subtree without ever listing
# it, and ``**/name`` matches at any depth including the root.
LAMBDA_ASSET_EXCLUDE = [
    # VCS + agent state (.claude/worktrees churn is the race trigger)
    ".git",
    ".claude",
    ".claude-tmp",
    ".autonomous-progress",
    # Python tool caches + virtualenvs
    "**/__pycache__",
    "**/.mypy_cache",
    "**/.pytest_cache",
    "**/.ruff_cache",
    "**/.venv",
    # Node dependencies + Vite cache
    "**/node_modules",
    "**/.vite",
    # Coverage outputs (root-level names match the repo's .gitignore)
    "**/coverage",
    "coverage-unit",
    "coverage-js",
    "coverage-combined",
    "coverage.xml",
    ".coverage*",
    "htmlcov",
    # Build outputs
    "ui/dist",
    "desktop/dist-main",
    "desktop/dist-renderer",
    "desktop/release",
    "docs-site/.vitepress/dist",
    "docs-site/.vitepress/cache",
    "**/cdk.out",
    # OS junk
    "**/.DS_Store",
]

# API Lambda Docker-bundling steps (#319). Module-level so the unit
# tests can assert the command shape — bundling commands never appear
# in the synthesized template (they only drive local asset staging),
# so a template assertion can't guard them.
#
# ``uv export`` MUST carry ``--no-emit-project``. Without it, the
# export includes the project itself as an editable requirement
# (``-e .``) and ``pip install`` builds the project inside the
# bundling container to generate its metadata. The build backend is
# hatchling + hatch-vcs, which resolves the version from ``.git`` —
# but in a git worktree (the agent workflow's default), ``.git`` is a
# pointer FILE referencing a gitdir OUTSIDE the bind mount, so
# version resolution fails (setuptools-scm LookupError: "unable to
# detect version for /asset-input") and bundling — and thus
# synth/deploy — dies.
#
# Dropping the project from the export is safe: the exported
# requirements still pin the full third-party closure, first-party
# code ships via the explicit ``cp -r src/channel`` step below (no
# dist-info entry point is load-bearing — ``run.sh`` starts uvicorn
# via plain module import), and the runtime's two self-version reads
# (``channel/api/main.py:_app_version`` and
# ``channel/logging_config.py``) both fall back to the
# ``APP_VERSION`` env var, which this stack always injects into the
# Lambda environment.
API_LAMBDA_BUNDLING_STEPS = [
    "pip install uv --quiet --no-cache-dir",
    # Export only third-party runtime deps — exclude the dev and infra
    # (CDK) groups and the project itself (see module comment above).
    # Kept as ONE literal (E501 is ignored repo-wide): implicit string
    # concatenation across lines invites a silently missing space.
    "UV_CACHE_DIR=/tmp/uv-cache uv export --no-hashes --no-group dev --no-group infra --no-emit-project -o /tmp/requirements.txt",
    "pip install -r /tmp/requirements.txt -t /asset-output --quiet --no-cache-dir",
    "cp -r src/channel /asset-output/channel",
    # run.sh is the AWSLWA entrypoint — must be executable at Lambda root
    "cp run.sh /asset-output/run.sh",
    "chmod +x /asset-output/run.sh",
]


# Path the violation reports POST to — ``src/channel/api/csp.py``, which logs
# each report and emits the ``CSPViolations`` EMF metric.
#
# ``report-uri`` is the ONLY reporting transport this policy carries, and that
# is a measured decision rather than an oversight — see the "reporting" section
# of ``_build_csp_header``'s docstring. Do not "modernise" it by adding a
# ``report-to`` directive alongside: doing so is what silenced reporting
# entirely from #196 until #598.
CSP_REPORT_PATH = "/api/csp-report"


def _build_csp_header(
    *,
    custom_domain: str,
    attachments_bucket_name: str,
    region: str,
) -> str:
    """Compose the CloudFront Content-Security-Policy header (#196, #598).

    The browser SPA needs ``connect-src`` to cover:
      * its own API origin (``https://{custom_domain}``) so the fetch
        wrappers in ``ui/src/api.js`` reach the management API
      * the attachments S3 bucket's virtual-host URLs so presigned
        PUT uploads succeed without CSP blocking

    Both us-east-1 legacy (``{bucket}.s3.amazonaws.com``) and regional
    (``{bucket}.s3.{region}.amazonaws.com``) variants are enumerated —
    the SDK can hand back either form depending on the resolution path.
    Replaces the prior ``https://channel.example.com`` placeholder
    that was never substituted per-env.

    **Still served Report-Only.** #598 measured what enforcing this policy
    would actually block, by driving the deployed dev stack with Chromium and
    reading the ``securitypolicyviolation`` DOM events (Report-Only fires the
    same events as enforcing, with ``disposition: "report"``). Three of the
    four violation classes it found are fixed here, each by naming exactly one
    additional source — no wildcard, no ``'unsafe-inline'``:

    * ``font-src`` — the Google Fonts ``@import`` in
      ``ui/src/styles/channel.css`` and ``docs-site/.vitepress/theme/style.css``
      pulls ~2 750 woff2 files from ``fonts.gstatic.com`` per drive. The
      directive was absent entirely, so it fell back to ``default-src 'self'``
      and every font on every page was a violation.
    * ``style-src-elem`` — the stylesheet those ``@import``s fetch, from
      ``fonts.googleapis.com``.
    * ``img-src`` — ``blob:``. ``useAssetContent`` renders image assets
      (#279 generated images, code-exec output) through a same-origin
      ``URL.createObjectURL`` blob, which ``data:`` does not cover.

    The fourth class, ``script-src-elem`` / ``inline``, is why the header is
    NOT flipped to enforcing in this change. It has three independent sources
    and none of them can be narrowed from here:

    * ``ui/index.html`` — the GA4 consent gate and the theme pre-paint script.
      Hashable in principle, but the GA block embeds a build-substituted
      measurement id, so the hash differs per environment and CDK cannot know
      it at synth time. The fix is to externalise both scripts.
    * the VitePress docs pages — three generated inline scripts per page, one
      of which embeds ``__VP_HASH_MAP__`` (per-page content hashes). No static
      hash list survives a docs edit, and VitePress exposes no nonce hook.
    * ``auth/mgmt_auth.py``'s login-completion page — an inline script whose
      body IS the freshly minted JWT, so it is unhashable by construction.
      Enforcing ``script-src 'self'`` blocks it and **breaks sign-in
      outright**.

    Closing those needs changes in ``ui/``, in the auth token-handoff path,
    and a separate response-headers policy for the ``/docs*`` behaviour —
    outside this issue's scope, and the auth half wants a human design call
    before an agent rewrites how the mgmt JWT reaches ``localStorage``.
    Everything else measured clean: no ``connect-src`` violation (including
    the presigned S3 PUT), no ``'unsafe-eval'`` requirement (mermaid renders
    without it), and no ``form-action`` / ``base-uri`` / ``frame-ancestors``
    hits across ~3 500 events.

    **Reporting: ``report-uri`` only, and the missing ``report-to`` is the
    point.** From #196 until #598 this policy ended
    ``report-uri /api/csp-report; report-to default;`` — and delivered
    nothing, ever. ``CSPViolations`` had never been emitted once: the metric
    did not exist in the ``Channel`` namespace at all, and 30 days of dev API
    logs contained no report. Two faults compounded:

    * no ``Reporting-Endpoints`` (or legacy ``Report-To``) header was served,
      so the group ``report-to default`` named did not exist; and
    * **Chromium suppresses ``report-uri`` whenever a ``report-to`` is
      present**, so the working transport was disabled by the broken one.

    Measured directly, serving one violating page under five header combos to
    Chromium (headless and headed, 75s delivery window, delivery counted
    server-side):

    ==================================================  ========
    header combination                                  reports
    ==================================================  ========
    ``report-uri`` only                                 **1**
    ``report-to`` + ``Reporting-Endpoints``             0
    ``report-uri`` + ``report-to`` + ``Reporting-Endpoints``  0
    ``report-uri`` + ``report-to``, group undeclared    0
    ``report-to`` + legacy ``Report-To``                0
    ==================================================  ========

    Declaring the endpoint group was tried first and deployed to the personal
    ``jc`` stack over HTTPS on its real domain: a full 21-surface drive still
    produced zero reports server-side. So the fix is to drop ``report-to``,
    not to declare it — carrying a Reporting-API directive that delivers
    nothing while disabling the one that works is strictly worse than not
    carrying it. Re-adding it needs evidence that delivery works, not a
    deprecation notice.
    """

    api_origin = f"https://{custom_domain}"
    bucket_legacy = f"https://{attachments_bucket_name}.s3.amazonaws.com"
    bucket_regional = f"https://{attachments_bucket_name}.s3.{region}.amazonaws.com"

    return (
        "default-src 'self'; "
        "script-src 'self' https://www.googletagmanager.com; "
        "connect-src 'self' https://www.google-analytics.com "
        f"{api_origin} {bucket_legacy} {bucket_regional}; "
        # blob: — same-origin object URLs only; it grants no third-party
        # origin. See the docstring for the asset-render path that needs it.
        "img-src 'self' data: blob: https://www.google-analytics.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        # NO `report-to` — see the docstring. Its presence suppresses
        # `report-uri` in Chromium, and the Reporting API delivered nothing in
        # its place. `report-uri` is deprecated but is the only transport
        # measured to actually work.
        f"report-uri {CSP_REPORT_PATH};"
    )


# ---------------------------------------------------------------------------
# Post-deploy assertion: prod alarms must be able to reach a human (#537)
# ---------------------------------------------------------------------------
#
# Every CloudWatch alarm below publishes to ``AlarmTopic``. None of that
# is worth anything if the topic has no subscriber able to receive a
# message — the state #537 found on both deployed stacks, where 24
# alarms existed and not one of them could notify anybody.
#
# **Prod only.** The product decision on #537 (2026-08-08) accepts
# ``dev`` and the personal ``jc`` stack as deliberately silent, so this
# never runs for them. The per-cold-start ``alarm-email`` placeholder
# WARNING stays a reminder and is explicitly *not* a control.
#
# **Why this is a post-deploy check and not a synth-time template
# assertion.** Confirmation state does not exist at synth time. A
# CloudFormation template can declare a subscription; it cannot say
# whether the recipient ever clicked the link, and an unconfirmed email
# subscription delivers nothing. ``PendingConfirmation`` is visible only
# by querying live SNS, so a synth-time assertion is structurally
# incapable of making the assertion that matters. See
# ``confirmed_subscription_arns``.

PROD_ENV_NAME = "prod"

#: CloudFormation output name the CI job reads to discover the topic.
ALARM_TOPIC_ARN_OUTPUT = "AlarmTopicArn"

#: Upper bound on ``ListSubscriptionsByTopic`` pages walked. SNS returns
#: up to 100 subscriptions per page and supplies the continuation token
#: itself; the cap exists so a misbehaving pagination response cannot
#: hang a deploy pipeline, not because 2 000 subscriptions is plausible
#: on an alarm topic.
#:
#: The walk stops at the first confirmed subscription, so the cap can
#: only bite when every one of the first 2 000 records is unconfirmed —
#: and then it reports a failure it has not fully ruled out rather than
#: one it has proven. That direction is deliberate: this gate exists to
#: catch alarms that reach nobody, so an unverifiable topic should read
#: as unverified. It is a false *failure* that is possible here, never a
#: false pass.
_MAX_SUBSCRIPTION_PAGES = 20


class AlarmSubscriptionError(RuntimeError):
    """The prod alarm topic cannot deliver a notification to anybody."""


def confirmed_subscription_arns(
    subscriptions: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Return the subscription ARNs that can actually deliver.

    ``SubscriptionArn`` is the only field carrying confirmation state.
    For a subscription whose recipient has not clicked through, SNS
    returns the literal string ``PendingConfirmation`` there instead of
    an ARN; ``Deleted`` appears the same way for one torn down while the
    page was being assembled. Both are subscription *records* that
    deliver nothing.

    The test is therefore an allowlist — "is this a real ARN" — and
    deliberately **not** a denylist of the known sentinel strings. A
    denylist would admit ``Deleted``, and would admit whatever sentinel
    SNS adds next, letting the check pass in exactly the
    delivers-nothing state it exists to catch (#537).
    """
    confirmed: list[str] = []
    for subscription in subscriptions:
        arn = subscription.get("SubscriptionArn")
        if isinstance(arn, str) and arn.startswith("arn:"):
            confirmed.append(arn)
    return confirmed


def require_confirmed_alarm_subscription(
    env_name: str,
    topic_arn: str,
    client_factory: Callable[[], Any],
) -> str:
    """Assert *topic_arn* has at least one confirmed subscription.

    Returns a one-line human-readable result on success. Raises
    ``AlarmSubscriptionError`` when prod's alarm topic cannot reach
    anybody.

    Non-prod is a no-op that touches AWS not at all — *client_factory*
    is never called, so no credentials are needed and no API error can
    be raised on a stack the decision has accepted as silent. Keeping
    the environment gate here rather than at the call site means there
    is exactly one of it.
    """
    if env_name != PROD_ENV_NAME:
        return (
            f"env={env_name!r} is not {PROD_ENV_NAME!r} — skipping the "
            "confirmed-alarm-subscription check (accepted as silent, #537)"
        )

    sns_client = client_factory()
    scanned = 0
    confirmed: list[str] = []
    next_token: str | None = None

    for _ in range(_MAX_SUBSCRIPTION_PAGES):
        kwargs: dict[str, Any] = {"TopicArn": topic_arn}
        if next_token:
            kwargs["NextToken"] = next_token
        page = sns_client.list_subscriptions_by_topic(**kwargs)
        subscriptions = page.get("Subscriptions") or []
        scanned += len(subscriptions)
        confirmed = confirmed_subscription_arns(subscriptions)
        if confirmed:
            break
        next_token = page.get("NextToken")
        if not next_token:
            break

    if not confirmed:
        raise AlarmSubscriptionError(
            f"Alarm topic {topic_arn} has NO confirmed subscription "
            f"({scanned} subscription record(s) scanned, 0 confirmed). Every "
            "CloudWatch alarm on this stack would fire into a void. Subscribe "
            "an address and CONFIRM it from the recipient's inbox — a record "
            "left at PendingConfirmation delivers nothing. Runbook: "
            "docs-site/ops/alarms.md (#537)."
        )

    return (
        f"Alarm topic {topic_arn} has {len(confirmed)} confirmed "
        f"subscription(s) among the {scanned} record(s) scanned."
    )


def _default_sns_client() -> Any:
    """Build a real SNS client.

    Imported lazily so a plain ``cdk synth`` never pays for boto3.
    """
    import boto3

    return boto3.client("sns")


def _main(argv: list[str] | None = None) -> int:
    """CLI entry point for the CI post-deploy step. Returns an exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m stacks.channel_stack",
        description=(
            "Fail unless the given SNS alarm topic has at least one CONFIRMED "
            "subscription. Prod only — see #537."
        ),
    )
    parser.add_argument("--env", required=True, help="Deployment environment name")
    parser.add_argument("--topic-arn", required=True, help="Alarm SNS topic ARN")
    args = parser.parse_args(argv)

    try:
        print(require_confirmed_alarm_subscription(args.env, args.topic_arn, _default_sns_client))
    except AlarmSubscriptionError as exc:
        # ``::error::`` renders as a job annotation on GitHub Actions.
        print(f"::error::{exc}")
        return 1
    return 0


class ChannelStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str = "prod",
        hosted_zone_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Apply cost-allocation tags to every resource in the stack.
        cdk.Tags.of(self).add("project", "channel")
        cdk.Tags.of(self).add("env", env_name)

        is_prod = env_name == "prod"

        # Non-prod stacks destroy resources on `cdk destroy` for easy teardown.
        # The JWT secret is always retained to prevent accidental key loss.
        data_removal = cdk.RemovalPolicy.RETAIN if is_prod else cdk.RemovalPolicy.DESTROY

        # GitHub Actions environment name used in the OIDC trust condition.
        # Must match the `environment:` key in the workflow job exactly.
        # prod → "production", dev → "development", others → env_name as-is.
        _github_env_map = {"prod": "production", "dev": "development"}
        github_env = _github_env_map.get(env_name, env_name)

        # ----------------------------------------------------------------
        # DynamoDB single table
        # ----------------------------------------------------------------
        # Table name is derived from env_name so arbitrary envs never conflict.

        table_name = "channel" if is_prod else f"channel-{env_name}"

        table = dynamodb.Table(
            self,
            "StarterTable",
            table_name=table_name,
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=data_removal,
            # PITR is expensive — only enable in prod
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=is_prod
            ),
            time_to_live_attribute="ttl",
        )

        # GSI 1 — KeyIndex: look up memories by key
        table.add_global_secondary_index(
            index_name="KeyIndex",
            partition_key=dynamodb.Attribute(name="GSI1PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="GSI1SK", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI 2 — TagIndex: list memories by tag
        table.add_global_secondary_index(
            index_name="TagIndex",
            partition_key=dynamodb.Attribute(name="GSI2PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="GSI2SK", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI 3 — UserEmailIndex: look up users by email
        table.add_global_secondary_index(
            index_name="UserEmailIndex",
            partition_key=dynamodb.Attribute(name="GSI4PK", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI 4 — ChatByIdIndex: look up chat-index rows by chat_id
        table.add_global_secondary_index(
            index_name="ChatByIdIndex",
            partition_key=dynamodb.Attribute(name="GSI3PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="GSI3SK", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI 5 — AssetOwnerIndex: cross-chat asset browsing by owner
        # (#324, epic #321). Attribute names are ``owner_pk`` /
        # ``owner_sk`` rather than GSI5PK/SK — the settled design keys
        # the index off a single ``owner`` value so the workspace-
        # tenancy migration (user_id → {workspace_id}/{user_id}) is a
        # one-attribute swap with no base-table repartition.
        table.add_global_secondary_index(
            index_name="AssetOwnerIndex",
            partition_key=dynamodb.Attribute(name="owner_pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="owner_sk", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI 6 — RefreshByUserIndex: list / bulk-revoke a user's
        # refresh-token rows (#290, epic #241). Sparse — only
        # ``REFRESH#{token_hash}`` rows carry GSI5PK/GSI5SK. The GSI5
        # slot is the next free numbered pair: GSI3 belongs to
        # ChatByIdIndex, GSI4 to UserEmailIndex, and AssetOwnerIndex
        # deliberately uses semantic ``owner_pk``/``owner_sk`` names
        # instead of a numbered slot (#324), so GSI5 was never taken.
        # Sort key is ``{issued_at}#{token_hash prefix}`` so a user's
        # rows come back in issue order; the #293 sessions list gets
        # newest-first by asking for it (``ScanIndexForward=False``)
        # rather than sorting client-side.
        table.add_global_secondary_index(
            index_name="RefreshByUserIndex",
            partition_key=dynamodb.Attribute(name="GSI5PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="GSI5SK", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ----------------------------------------------------------------
        # SSM Parameters
        # ----------------------------------------------------------------
        # All parameters use per-environment paths to prevent secret sharing.
        # Prod keeps legacy paths (no env suffix) for backward compatibility.
        def _ssm_path(name: str) -> str:
            return f"/channel/{name}" if is_prod else f"/channel/{env_name}/{name}"

        ssm_param_name = _ssm_path("jwt-secret")

        jwt_secret_param = ssm.StringParameter(
            self,
            "JwtSecret",
            parameter_name=ssm_param_name,
            string_value="CHANGE_ME_ON_FIRST_DEPLOY",
            description=f"Channel JWT signing secret ({env_name}) — rotate after first deploy",
            tier=ssm.ParameterTier.STANDARD,
        )
        # Always retain the JWT secret — losing it invalidates all issued tokens.
        jwt_secret_param.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        google_client_id_param = ssm.StringParameter(
            self,
            "GoogleClientId",
            parameter_name=_ssm_path("google-client-id"),
            string_value="CHANGE_ME_ON_FIRST_DEPLOY",
            description=f"Google OAuth 2.0 client ID ({env_name})",
            tier=ssm.ParameterTier.STANDARD,
        )
        google_client_id_param.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        google_client_secret_param = ssm.StringParameter(
            self,
            "GoogleClientSecret",
            parameter_name=_ssm_path("google-client-secret"),
            string_value="CHANGE_ME_ON_FIRST_DEPLOY",
            description=f"Google OAuth 2.0 client secret ({env_name})",
            tier=ssm.ParameterTier.STANDARD,
        )
        google_client_secret_param.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        allowed_emails_param = ssm.StringParameter(
            self,
            "AllowedEmails",
            parameter_name=_ssm_path("allowed-emails"),
            string_value="[]",
            description=f"JSON array of Google email addresses allowed to access Channel ({env_name}); empty = deny all (must be populated post-deploy)",
            tier=ssm.ParameterTier.STANDARD,
        )
        allowed_emails_param.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        # OriginVerifySecret is intentionally Type=String, NOT SecureString.
        # CfnDynamicReferenceService.SSM (used to inject the value into
        # CloudFront's origin custom headers) cannot resolve SecureString
        # parameters. The secret is defense-in-depth — preventing direct
        # Function URL access from outside the AWS account — not crypto.
        # The IAM-gated visibility on SSM and CloudFront origin config is
        # the security boundary. Rotating to SecureString breaks the deploy.
        # See docs-site/operations/security.md for the rotation runbook.
        origin_verify_param = ssm.StringParameter(
            self,
            "OriginVerifySecret",
            parameter_name=_ssm_path("origin-verify-secret"),
            string_value="CHANGE_ME_ON_FIRST_DEPLOY",
            description=f"CloudFront → Lambda shared secret for X-Origin-Verify header ({env_name})",
            tier=ssm.ParameterTier.STANDARD,
        )
        origin_verify_param.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        # Email address that receives CloudWatch alarm + recovery notifications.
        # Set the parameter value in SSM after first deploy, then confirm the
        # auto-created SNS subscription from your inbox. See
        # docs-site/ops/alarms.md for the first-deploy checklist.
        alarm_email_param = ssm.StringParameter(
            self,
            "AlarmEmail",
            parameter_name=_ssm_path("alarm-email"),
            string_value="CHANGE_ME_ON_FIRST_DEPLOY",
            description=f"Recipient for CloudWatch alarm notifications ({env_name})",
            tier=ssm.ParameterTier.STANDARD,
        )
        alarm_email_param.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        # ----------------------------------------------------------------
        # Shared Lambda code (Docker-bundled at cdk deploy time)
        # ----------------------------------------------------------------
        lambda_code = lambda_.Code.from_asset(
            _ASSET_ROOT,
            exclude=LAMBDA_ASSET_EXCLUDE,
            bundling=cdk.BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                command=["bash", "-c", " && ".join(API_LAMBDA_BUNDLING_STEPS)],
            ),
        )

        # ----------------------------------------------------------------
        # AWS Lambda Web Adapter layer
        # Enables streaming responses via Function URL RESPONSE_STREAM mode.
        # Update the version suffix when a new AWSLWA release is available:
        # https://github.com/awslabs/aws-lambda-web-adapter/releases
        # ----------------------------------------------------------------
        awslwa_layer = lambda_.LayerVersion.from_layer_version_arn(
            self,
            "AwsLambdaWebAdapterLayer",
            f"arn:aws:lambda:{self.region}:753240598075:layer:LambdaAdapterLayerX86:24",
        )

        # JWT issuer URL embedded in tokens — must be unique per environment.
        issuer_host = "channel" if is_prod else f"channel-{env_name}"
        custom_domain = f"{issuer_host}.{HOSTED_ZONE_NAME}"

        # ----------------------------------------------------------------
        # Route53 hosted zone + ACM certificate
        # ----------------------------------------------------------------
        # hosted_zone_id is passed as CDK context (-c hosted_zone_id=...) so that
        # the synth step in CI works without live AWS credentials.
        hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
            self,
            "HostedZone",
            hosted_zone_id=hosted_zone_id,
            zone_name=HOSTED_ZONE_NAME,
        )

        # ACM certificate must be in us-east-1 for CloudFront — this stack
        # deploys to us-east-1 by default, so no cross-region cert needed.
        certificate = acm.Certificate(
            self,
            "Certificate",
            domain_name=custom_domain,
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )

        app_version = os.environ.get("APP_VERSION", "dev")
        common_env = {
            "CHANNEL_TABLE_NAME": table.table_name,
            # Custom domain is the canonical issuer URL for all environments.
            "CHANNEL_ISSUER": f"https://{custom_domain}",
            # Tell both Lambdas which SSM parameter holds the JWT secret.
            "CHANNEL_JWT_SECRET_PARAM": ssm_param_name,
            # Google OAuth 2.0 SSM parameter paths
            "GOOGLE_CLIENT_ID_PARAM": google_client_id_param.parameter_name,
            "GOOGLE_CLIENT_SECRET_PARAM": google_client_secret_param.parameter_name,
            "ALLOWED_EMAILS_PARAM": allowed_emails_param.parameter_name,
            "CHANNEL_ORIGIN_VERIFY_PARAM": origin_verify_param.parameter_name,
            # Operational SSM parameter — soft-warn check at startup logs if
            # still set to the CHANGE_ME_ON_FIRST_DEPLOY placeholder.
            "CHANNEL_ALARM_EMAIL_PARAM": alarm_email_param.parameter_name,
            # APP_VERSION is injected at deploy time via the APP_VERSION env var.
            # Falls back to "dev" for local synth/deploy without a version set.
            "APP_VERSION": app_version,
            # Used by EMF metrics as the "Environment" dimension.
            "CHANNEL_ENV": env_name,
        }

        # Tag every resource with the deployed version for operational visibility.
        cdk.Tags.of(self).add("version", app_version)

        # ----------------------------------------------------------------
        # Management API Lambda
        # ----------------------------------------------------------------
        api_role = iam.Role(
            self,
            "ApiLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )
        table.grant_read_write_data(api_role)
        jwt_secret_param.grant_read(api_role)
        google_client_id_param.grant_read(api_role)
        google_client_secret_param.grant_read(api_role)
        allowed_emails_param.grant_read(api_role)
        origin_verify_param.grant_read(api_role)
        # Soft-warn startup check needs to read the alarm-email parameter to
        # detect the CHANGE_ME_ON_FIRST_DEPLOY placeholder.
        alarm_email_param.grant_read(api_role)
        # #182 — Exa API key is created externally (not by CDK) as a
        # SecureString; we only grant read. Use
        # ``from_secure_string_parameter_attributes`` (NOT
        # ``from_string_parameter_attributes``) because the underlying
        # SSM parameter type is SecureString — CloudFormation refuses
        # to dynamic-reference SecureString params (would leak the
        # decrypted value into CFN logs, hence the deploy-time
        # "Parameters have types not supported by CloudFormation"
        # error from the round-1 attempt). The secure variant skips
        # the CFN parameter dance and just grants IAM on the ARN;
        # runtime boto3 ``ssm.get_parameter(WithDecryption=True)``
        # fetches the value on the first ``web_search()`` invocation.
        exa_key_param = ssm.StringParameter.from_secure_string_parameter_attributes(
            self,
            "ExaApiKeyParam",
            parameter_name=f"/channel/{env_name}/exa-api-key",
        )
        exa_key_param.grant_read(api_role)
        # #236 — the admin metrics endpoints (/api/admin/metrics/*) read
        # the Channel-namespace EMF counters back via GetMetricData.
        # ``resources=["*"]`` IS the minimal grant: GetMetricData supports
        # no resource types and no condition keys — the
        # ``cloudwatch:namespace`` condition applies only to PutMetricData
        # (Service Authorization Reference for Amazon CloudWatch), so the
        # namespace cannot be pinned in IAM. The read surface is bounded
        # instead by the server-side metric allowlist in
        # ``src/channel/api/admin.py`` (admin-JWT-gated endpoints).
        # Asserted by ``test_api_lambda_role_grants_cloudwatch_read`` in
        # ``tests/unit/test_channel_stack.py``.
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:GetMetricData", "cloudwatch:DescribeAlarms"],
                resources=["*"],
            )
        )
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ce:GetCostAndUsage"],
                resources=["*"],
            )
        )
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    # Foundation models — wildcarded across regions because the
                    # ``us.*`` cross-region inference profiles below fan invocations
                    # out to whichever member region has capacity (us-east-1,
                    # us-east-2, or us-west-2). Bedrock authorizes against BOTH
                    # the inference-profile ARN and the underlying foundation-model
                    # ARN it routes to, so a region-pinned foundation-model ARN
                    # fails with AccessDeniedException whenever Bedrock picks a
                    # region other than ``self.region``.
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-opus-4-6-v1",
                    # US cross-region inference profiles (us-east-1 primary)
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-sonnet-4-6",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-opus-4-6-v1",
                    # Stability text-to-image generation (#279 — generate_image
                    # tool). CROSS-REGION: these models are ACTIVE only in
                    # us-west-2 (absent from us-east-1, and no us-east-1
                    # cross-region inference profile exists for them), so the
                    # tool's bedrock-runtime client targets us-west-2 and the
                    # ARN region segment is HARDCODED us-west-2 — NOT this
                    # stack's region (``self.region`` is us-east-1). Empty
                    # account segment (``::``) is the foundation-model ARN
                    # shape. All three generators are granted so a
                    # ``CHANNEL_IMAGE_GEN_MODEL`` switch to Ultra / SD3.5 Large
                    # needs no redeploy — still least-privilege (three pinned
                    # ARNs, no wildcard).
                    "arn:aws:bedrock:us-west-2::foundation-model/stability.stable-image-core-v1:1",
                    "arn:aws:bedrock:us-west-2::foundation-model/stability.stable-image-ultra-v1:1",
                    "arn:aws:bedrock:us-west-2::foundation-model/stability.sd3-5-large-v1:0",
                ],
            )
        )
        # Bedrock AgentCore Memory — Phase 7c writes one event per chat
        # turn. The Memory resource is created lazily at first request,
        # so the Lambda needs both control-plane (find-or-create) and
        # data-plane (read/write events) access. List* actions are
        # account-scoped; the rest are pinned to channel_{env}* (the
        # asterisk covers the opaque suffix AgentCore appends to memory
        # ids on creation). Name uses underscores not hyphens —
        # AgentCore's name validator is ``[a-zA-Z][a-zA-Z0-9_]{0,47}``.
        agentcore_memory_arn = (
            f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:memory/channel_{env_name}*"
        )
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    # NOTE: IAM action prefix is ``bedrock-agentcore:`` for
                    # BOTH the data plane (CreateEvent / ListEvents / etc.)
                    # AND the control plane (CreateMemory / GetMemory /
                    # ListMemories). The boto3 client distinction
                    # (``bedrock-agentcore`` vs ``bedrock-agentcore-control``)
                    # is a client-library convenience; AWS service
                    # authorization uses the single ``bedrock-agentcore``
                    # prefix per the Service Authorization Reference.
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:ListSessions",  # Phase 8a recall
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:DeleteEvent",
                    "bedrock-agentcore:CreateMemory",
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:RetrieveMemoryRecords",  # Phase 7d recall (retained for future use)
                ],
                resources=[agentcore_memory_arn],
            )
        )
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:ListMemories"],
                resources=["*"],
            )
        )

        # Phase 7c — dev-only debug endpoints under /api/_debug/* are
        # gated by this env var at FastAPI app-construction time. Set it
        # ONLY on non-prod envs; the corresponding CDK assertion test
        # (``tests/unit/test_channel_stack.py``) guards against leaks.
        if not is_prod:
            common_env["CHANNEL_ENABLE_DEBUG_ENDPOINTS"] = "1"
            # #179 — Google-auth bypass for e2e suites. Gates two distinct
            # short-circuits on /auth/login:
            #   - ``?test_email=`` query → mint a JWT directly (only fires
            #     when the query param is present; normal browser flows are
            #     unaffected).
            #   - ``desktop_callback=...`` query → only fires when this
            #     stack ALSO sets ``CHANNEL_DESKTOP_DEV_EMAIL``, which it
            #     deliberately does NOT (only ``inv desktop-dev`` sets it
            #     locally). So real desktop sign-in on this stack routes
            #     through Google like any other browser flow.
            # Prod stacks MUST NOT set this; a sibling assertion in
            # ``tests/unit/test_channel_stack.py`` guards against the same
            # leak shape as the debug-endpoints flag.
            common_env["CHANNEL_BYPASS_GOOGLE_AUTH"] = "1"

        # #181 chassis — register ``current_time`` smoke-test tool only
        # in non-prod envs (strategy spec policy P2: smoke-test, not a
        # product feature). Prod gets the flag explicitly set to ``"0"``
        # so a future code path that reads it can't accidentally see an
        # unset variable as "on" if defaults shift. A sibling assertion
        # in ``tests/unit/test_channel_stack.py`` enforces both shapes.
        common_env["CHANNEL_CLOCK_TOOL_ENABLED"] = "0" if is_prod else "1"

        # #182 web search — Exa API key path in SSM, resolved lazily on
        # the first ``web_search()`` invocation (NOT at Lambda cold-start)
        # and cached for the warm pool's lifetime; see
        # ``src/channel/agents/tools/web_search._resolve_exa_api_key``.
        common_env["CHANNEL_EXA_API_KEY_PARAM"] = f"/channel/{env_name}/exa-api-key"
        # Default enabled in every env; flag is a kill switch, not a rollout knob
        common_env["CHANNEL_WEB_SEARCH_ENABLED"] = "1"

        # #279 Stable Image Core image generation — kill switch only,
        # default-on in every deployed env (image gen is v0.1 scope). Set
        # explicitly so a future default-shift can't flip it on/off by
        # accident; the IAM grant for the us-west-2 Stability generators
        # rides the InvokeModel statement above. Billing is deferred, so
        # there is no cost/quota gate — this flag is the only control
        # besides the EMF counter. CHANNEL_IMAGE_GEN_REGION is pinned to
        # us-west-2 here (the image models' only region — this is the app's
        # sole cross-region call; the rest of the stack stays us-east-1) so
        # the deployed region is auditable in the template rather than only
        # a code default.
        common_env["CHANNEL_IMAGE_GEN_ENABLED"] = "1"
        common_env["CHANNEL_IMAGE_GEN_REGION"] = "us-west-2"

        # #111 per-request metrics — the per-``Route`` EMF dimension set is
        # the cost lever on this feature (one extra custom metric per route
        # that receives traffic; the aggregate ``{Environment}`` series that
        # alarms and the admin dashboard read is unaffected). Set explicitly
        # for the same reason as the flags above: the deployed value is
        # auditable in the template rather than only a code default, and a
        # future default-shift can't flip it by accident. Flip to ``"0"`` to
        # drop the breakdown — Logs Insights answers the same question from
        # the structured request log lines for free.
        common_env["CHANNEL_REQUEST_ROUTE_DIMENSION_ENABLED"] = "1"

        # #207 MCP registry — dedicated CMK + redirect-URI env + IAM.
        # The CMK has annual rotation enabled and is destroyed on stack
        # teardown only in non-prod envs (data_removal mirrors the other
        # stateful resources). Token blobs encrypted by it never leave
        # DynamoDB; losing the key on prod would mean every user has to
        # re-authenticate every registered MCP server, which is recoverable.
        mcp_token_key = kms.Key(
            self,
            "MCPTokenKey",
            description=f"Channel {env_name} — encrypts MCP OAuth token blobs at rest",
            enable_key_rotation=True,
            removal_policy=data_removal,
        )
        mcp_token_key.grant_encrypt_decrypt(api_role)
        common_env["CHANNEL_MCP_TOKEN_KMS_KEY_ID"] = mcp_token_key.key_arn
        common_env["CHANNEL_MCP_REGISTRY_ENABLED"] = "1"

        # #561 — widen the per-server MCP tool budget from the code default
        # of 24 (``_DEFAULT_MCP_MAX_TOOLS_PER_SERVER`` in
        # ``src/channel/api/chats.py``) to 35. The override exists precisely
        # so the deployed budget can differ from the library default; the
        # default itself stays put.
        #
        # WHY 35 AND NOT 24: the GitHub MCP server advertises 47 tools, of
        # which **28 are read-only**. #536 / #560 made the cap prefer
        # read-only tools when truncating — correctly, since the pre-#560
        # name-ordered selection kept ``api_delete_file`` and
        # ``api_create_repository`` while dropping ``api_list_issues``. But
        # with a budget of 24 against 28 reads, every slot went to a read, 4
        # reads still spilled, and all 19 write tools were dropped —
        # including ``api_issue_write``, so Channel could not create issues,
        # comment, open PRs or push. 35 fits the whole read surface (taking
        # ``dropped_read_only`` to 0, which is what #536 was actually after)
        # plus 7 write slots.
        #
        # THE COST IS REAL: #389's rationale for having a cap at all is
        # token overhead — every enumerated tool's JSON schema ships in the
        # Bedrock Converse ``toolConfig`` on EVERY turn, ~15-20K tokens for
        # a heavy server. Raising the budget buys write coverage with
        # context window.
        #
        # THIS IS A BANDAID SIZED TO ONE OBSERVED SERVER, not a principled
        # limit — 35 is "28 observed reads plus a few", and a different
        # server with a different tool count gets no better answer from it.
        # The durable fix is #536 option 3, a per-server allowlist, which
        # #560 identified as the phase-2 follow-on: it would keep this
        # server's 28 reads plus the handful of writes actually wanted
        # (``issue_write``, ``add_issue_comment``, ``create_pull_request``)
        # and drop the rest, instead of buying write slots by raising a
        # global budget.
        common_env["CHANNEL_MCP_MAX_TOOLS_PER_SERVER"] = "35"
        # The redirect URI is the API origin's /auth/mcp/callback path.
        # MCP servers persist this in their DCR client record; changing
        # it later requires re-registering, so derive it from the env's
        # custom_domain.
        common_env["CHANNEL_MCP_REDIRECT_URI"] = f"https://{custom_domain}/auth/mcp/callback"
        common_env["CHANNEL_SPA_BASE_URL"] = f"https://{custom_domain}"

        # ----------------------------------------------------------------
        # Attachments S3 bucket (#173) — file attachments + vision (epic #109)
        # ----------------------------------------------------------------
        # Browser uploads land here via presigned PUT (presign + finalize
        # endpoints arrive in #175). Bedrock fetches the bytes directly via
        # Strands' s3Location source — Lambda never holds the payload.
        #
        # CORS PUT must be allowed from the SPA origin. In prod that's the
        # CloudFront custom domain; in dev/personal envs we also allow the
        # localhost port range that ``inv dev`` uses (mirrors CORS_ORIGINS
        # in tasks.py around line 432).
        attachments_cors_origins = [f"https://{custom_domain}"]
        if not is_prod:
            attachments_cors_origins += [f"http://localhost:{port}" for port in range(5173, 5180)]

        # The Electron desktop app loads the SPA from the custom ``app://``
        # scheme (``desktop/main/window.js`` → ``loadURL("app://-/app")``;
        # the scheme is registered ``standard: true`` in
        # ``desktop/main/protocol.js``), so the desktop renderer's Origin is
        # the fixed string ``app://-`` — not an opaque ``null``. The presigned
        # browser→S3 attachment PUT is a cross-origin request, so this origin
        # must be in the bucket CORS allowlist or the desktop upload is blocked
        # while Chrome (allowed via the domain above) works (#378). Added
        # unconditionally: the desktop origin is env-independent.
        attachments_cors_origins.append("app://-")

        # SSE-KMS via the AWS-managed ``aws/s3`` key. No dedicated CMK —
        # the AWS-managed key auto-grants any IAM principal in the account
        # that has the matching ``s3:`` permission on the bucket. The
        # IAM grants below add the canonical kms:ViaService statement so
        # encrypt/decrypt is wired explicitly for the Lambda role.
        attachments_bucket = s3.Bucket(
            self,
            "AttachmentsBucket",
            encryption=s3.BucketEncryption.KMS_MANAGED,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=data_removal,
            auto_delete_objects=not is_prod,
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.PUT],
                    allowed_origins=attachments_cors_origins,
                    allowed_headers=["*"],
                    exposed_headers=["ETag"],
                    max_age=3000,
                ),
            ],
            lifecycle_rules=[
                # Presign tags new objects ``unreferenced=1``; finalize
                # (#175) removes the tag. Objects that still carry the
                # tag after 24h get garbage-collected — the typical
                # "user picks a file then cancels" leak. S3 lifecycle's
                # smallest expiration unit is one day, so actual deletion
                # runs between 24h and ~48h after upload depending on the
                # daily sweep.
                s3.LifecycleRule(
                    id="ExpireUnreferencedUploads",
                    enabled=True,
                    expiration=cdk.Duration.days(1),
                    tag_filters={"unreferenced": "1"},
                ),
            ],
        )

        # IAM grants — scoped to the ``attachments/user/*`` prefix so the
        # Lambda role can never reach into other buckets or other prefixes.
        attachments_bucket.grant_put(api_role, "attachments/user/*")
        attachments_bucket.grant_read(api_role, "attachments/user/*")
        attachments_bucket.grant_delete(api_role, "attachments/user/*")
        # #324 — asset payloads (epic #321) reuse this bucket under the
        # ``assets/chat/{chat_id}/{asset_id}`` prefix (shared-infra
        # product decision: no second bucket). Same prefix-scoped
        # grant discipline; no dedicated tagging statement — assets are
        # server-created and never carry the ``unreferenced=1``
        # lifecycle tag that presigned uploads manipulate (grant_put's
        # standard write action set does bundle s3:PutObjectTagging,
        # which is harmless here).
        assets_prefix = "assets/chat/*"
        attachments_bucket.grant_put(api_role, assets_prefix)
        attachments_bucket.grant_read(api_role, assets_prefix)
        attachments_bucket.grant_delete(api_role, assets_prefix)
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:PutObjectTagging", "s3:DeleteObjectTagging"],
                resources=[attachments_bucket.arn_for_objects("attachments/user/*")],
            )
        )
        # CDK's grant_read does NOT inject KMS permissions when the bucket
        # uses ``KMS_MANAGED`` (no key construct exists to grant against).
        # The aws/s3 key policy auto-grants account principals, but only
        # for calls coming through the S3 service — codify that with the
        # canonical kms:ViaService condition so the IAM role's intent is
        # explicit at synth time.
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=["kms:Decrypt", "kms:GenerateDataKey"],
                resources=["*"],
                conditions={
                    "StringEquals": {
                        "kms:ViaService": f"s3.{self.region}.amazonaws.com",
                    },
                },
            )
        )

        common_env["CHANNEL_ATTACHMENTS_BUCKET"] = attachments_bucket.bucket_name

        # Server access logging is deferred — CloudTrail data events plus
        # the ChatDeleteAttachmentWipeFailures EMF metric (added in #174)
        # cover the v1 audit needs for attachment lifecycle.
        NagSuppressions.add_resource_suppressions(
            attachments_bucket,
            [
                NagPackSuppression(
                    id="AwsSolutions-S1",
                    reason=(
                        "Server access logging deferred; CloudTrail data events "
                        "and ChatDeleteAttachmentWipeFailures EMF metric cover "
                        "v1 audit needs."
                    ),
                ),
            ],
        )

        # ─── Code-exec sandbox (#183, network isolation #249) ────────
        # Separate Lambda with its own IAM role. Two independent
        # boundaries:
        #
        # 1. IAM — AWSLambdaBasicExecutionRole (CloudWatch Logs
        #    writes) plus AWSLambdaVPCAccessExecutionRole (ENI
        #    lifecycle for the VPC attachment; ec2:*NetworkInterface*
        #    only) — no DynamoDB, S3, Bedrock, Secrets, or SSM
        #    grants. Even if user code escapes the subprocess (it
        #    shouldn't), the sandbox can't reach Channel data.
        # 2. Network (#249) — the Lambda runs in a dedicated
        #    PRIVATE_ISOLATED VPC: no internet gateway, no NAT, no
        #    VPC endpoints, plus a security group with zero egress
        #    rules as defense in depth. Untrusted content (web
        #    search #182, MCP #207, attachments) can steer the model
        #    into code-exec; without this, that code had outbound
        #    internet — the prompt-injection → code-exec →
        #    exfiltration trifecta. Threat model:
        #    docs/security/threat-model-code-exec.md.
        #
        # CloudWatch logging still works — Lambda logs through the
        # service plane, not the VPC network path. SnapStart is
        # compatible with VPC (not on the documented incompatibility
        # list: provisioned concurrency, EFS, >512 MB ephemeral).
        # SnapStart on published versions masks the sci-stack imports.
        #
        # AZs are pinned explicitly (never a context lookup): the
        # region defaults to us-east-1 in infra/app.py (overridable
        # via `-c region=`, though CloudFront/WAF cert handling
        # assumes us-east-1 in practice), and letting ec2.Vpc resolve
        # AZs would trigger an account-scoped context provider call
        # in `inv synth` and write the account id into
        # infra/cdk.context.json. The `{region}a`/`{region}b` suffixes
        # exist in every standard region; revisit if this ever deploys
        # to an opt-in region with remapped AZs.
        sandbox_vpc = ec2.Vpc(
            self,
            "CodeExecVpc",
            availability_zones=[f"{self.region}a", f"{self.region}b"],
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="CodeExecIsolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                )
            ],
        )
        NagSuppressions.add_resource_suppressions(
            sandbox_vpc,
            [
                NagPackSuppression(
                    id="AwsSolutions-VPC7",
                    reason=(
                        "Zero-egress isolated VPC for the code-exec sandbox: "
                        "no IGW, no NAT, no VPC endpoints, and the only "
                        "attached workload carries a zero-egress security "
                        "group — there is no network traffic to log. Flow "
                        "Logs would add cost for an empty channel."
                    ),
                ),
            ],
        )
        sandbox_sg = ec2.SecurityGroup(
            self,
            "CodeExecSandboxSg",
            vpc=sandbox_vpc,
            allow_all_outbound=False,  # zero egress rules — #249
            description="Zero-egress SG for the code-exec sandbox (#249)",
        )
        sandbox_role = iam.Role(
            self,
            "CodeExecLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                # ENI create/describe/delete for the #249 VPC
                # attachment. CDK only auto-attaches this when it
                # creates the function's role itself; this role is
                # explicit, so the attachment must be too. Grants
                # ec2:*NetworkInterface* actions only — no data access.
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
            ],
        )
        code_exec_fn = lambda_.Function(
            self,
            "CodeExecLambda",
            function_name=f"channel-{env_name}-code-exec",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="channel.sandbox.handler.lambda_handler",
            code=lambda_.Code.from_asset(
                # Asset root mirrors the API Lambda's ``from_asset``
                # — the pinned repo/worktree root (see ``_ASSET_ROOT``),
                # which holds ``src/channel/sandbox/``. The bundling
                # command pulls only what the sandbox needs (no FastAPI,
                # uvicorn, or Strands tree).
                _ASSET_ROOT,
                exclude=LAMBDA_ASSET_EXCLUDE,
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_13.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        (
                            "pip install -r src/channel/sandbox/requirements.txt "
                            "-t /asset-output && "
                            "mkdir -p /asset-output/channel/sandbox && "
                            "touch /asset-output/channel/__init__.py && "
                            "cp src/channel/sandbox/__init__.py "
                            "/asset-output/channel/sandbox/__init__.py && "
                            "cp src/channel/sandbox/handler.py "
                            "/asset-output/channel/sandbox/handler.py"
                        ),
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
            vpc=sandbox_vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[sandbox_sg],
        )

        api_fn = lambda_.Function(
            self,
            "ApiFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            # run.sh is the AWSLWA entrypoint — it starts uvicorn on port 8080.
            # AWSLWA intercepts Lambda invocations and proxies them as HTTP requests.
            handler="run.sh",
            code=lambda_code,
            role=api_role,
            environment={
                **common_env,
                # Tell the Python 3.12 managed runtime to delegate to AWSLWA's
                # bootstrap wrapper before invoking the handler. Without this,
                # the runtime treats `run.sh` as a dotted Python path and
                # tries `import run`, raising Runtime.ImportModuleError.
                "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",
                # Tell AWSLWA to use response-streaming mode so SSE responses
                # are streamed through the Function URL without buffering.
                "AWS_LWA_INVOKE_MODE": "response_stream",
                # AWSLWA looks for the web server on this port (default 8080).
                "PORT": "8080",
            },
            layers=[awslwa_layer],
            memory_size=512,
            # 5 min for streaming chats — Bedrock responses on long prompts can
            # exceed 30s. AWSLWA streams to the Function URL as bytes arrive, so
            # the long handler runtime doesn't add user-perceived latency.
            timeout=cdk.Duration.minutes(5),
            description=f"Channel management API (FastAPI + AWSLWA) [{env_name}]",
            tracing=lambda_.Tracing.ACTIVE,
        )

        # CDK names functions "{StackId}-{LogicalId}-{RandomSuffix}".
        # construct_id is the first segment, e.g. "ChannelStack-dev".
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:FilterLogEvents",
                    "logs:DescribeLogGroups",
                ],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/{construct_id}-*",
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/{construct_id}-*:*",
                ],
            )
        )

        # Grant the API Lambda permission to invoke the sandbox version (#183).
        # InvokeFunction on the published version so SnapStart-eligible
        # invocations route to the warm snapshot.
        code_exec_fn.current_version.grant_invoke(api_fn)
        api_fn.add_environment(
            "CHANNEL_CODE_EXEC_LAMBDA_ARN",
            code_exec_fn.current_version.function_arn,
        )
        api_fn.add_environment("CHANNEL_CODE_EXEC_ENABLED", "1")

        api_url = api_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            # RESPONSE_STREAM allows AWSLWA to stream SSE responses without buffering.
            invoke_mode=lambda_.InvokeMode.RESPONSE_STREAM,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.ALL],
                allowed_headers=["*"],
            ),
        )

        # ----------------------------------------------------------------
        # S3 bucket + CloudFront distribution for the React management UI
        # ----------------------------------------------------------------
        ui_bucket = s3.Bucket(
            self,
            "UiBucket",
            removal_policy=data_removal,
            auto_delete_objects=not is_prod,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireOldAutoUpdateBundles",
                    enabled=True,
                    expiration=cdk.Duration.days(30),
                    tag_filters={"channel-asset-type": "versioned-zip"},
                ),
            ],
        )

        # API origin — strip "https://" prefix and trailing "/" from the function URL
        api_origin_domain = cdk.Fn.select(2, cdk.Fn.split("/", api_url.url))

        # CloudFront injects X-Origin-Verify in every environment so Lambda
        # can reject direct Function URL access. The header value is read
        # from SSM at synth time (in tasks.py deploy) and passed in via the
        # `origin_verify_secret` CDK context value.
        #
        # Previously we used CfnDynamicReference(SSM, ...) to resolve the
        # SSM value at deploy time, but CFN did not reliably re-resolve the
        # token when SSM changed: a second deploy after `aws ssm put-parameter
        # --overwrite` left the CloudFront resource untouched, so the
        # placeholder secret stayed baked into the origin custom-header. The
        # synth-time read is explicit — every `inv deploy` pulls the current
        # SSM value and embeds it directly. See warlordofmars/channel#5 and
        # warlordofmars/agentcore-starter#158 (finding #2) for the history.
        #
        # The SSM parameter resource above is still the source of truth —
        # ops writes to it directly. This stack just reads it at synth time
        # for the CloudFront copy. Type=String is unchanged (the security
        # posture isn't affected: CFN already substitutes {{resolve:ssm:}}
        # to a literal in the deployed template).
        #
        # `CHANGE_ME_ON_FIRST_DEPLOY` is the fallback when no context value
        # is passed (e.g. raw `cdk synth` for inspection) — the synth still
        # renders cleanly so smoke tools and CI synth gates work, but a
        # deploy without the context value produces a non-functional stack.
        origin_verify_secret = (
            self.node.try_get_context("origin_verify_secret") or "CHANGE_ME_ON_FIRST_DEPLOY"
        )
        origin_verify_header = {"X-Origin-Verify": origin_verify_secret}

        api_cf_origin = origins.HttpOrigin(
            api_origin_domain,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
            origin_ssl_protocols=[cloudfront.OriginSslPolicy.TLS_V1_2],
            custom_headers=origin_verify_header,
        )

        # ----------------------------------------------------------------
        # CloudFront response headers — security hardening
        #
        # CSP is still Report-Only. #598 measured what enforcing would block
        # (see `_build_csp_header`): three of the four violation classes are
        # now allowlisted by exact source, and the fourth — inline `<script>`
        # on the SPA shell, the VitePress docs pages, and the login-completion
        # page — cannot be narrowed from this file. Enforcing today breaks
        # sign-in. Do NOT flip this header without first closing that class.
        # ----------------------------------------------------------------
        # Violations POST to /api/csp-report on the same origin; the endpoint
        # is unauthenticated + per-IP rate-limited. `report-uri` is the only
        # transport, deliberately — see `_build_csp_header`.
        csp_report_only = _build_csp_header(
            custom_domain=custom_domain,
            attachments_bucket_name=attachments_bucket.bucket_name,
            region=self.region,
        )
        security_headers_policy = cloudfront.ResponseHeadersPolicy(
            self,
            "StarterSecurityHeadersPolicy",
            response_headers_policy_name=f"channel-security-headers-{env_name}",
            comment="Channel security response headers (HSTS, CSP-RO, frame/referrer/permissions)",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=cdk.Duration.seconds(31536000),
                    include_subdomains=True,
                    preload=True,
                    override=True,
                ),
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(
                    override=True,
                ),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.DENY,
                    override=True,
                ),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True,
                ),
            ),
            custom_headers_behavior=cloudfront.ResponseCustomHeadersBehavior(
                custom_headers=[
                    cloudfront.ResponseCustomHeader(
                        header="Permissions-Policy",
                        value="camera=(), microphone=(), geolocation=(), payment=()",
                        override=True,
                    ),
                    cloudfront.ResponseCustomHeader(
                        header="Content-Security-Policy-Report-Only",
                        value=csp_report_only,
                        override=True,
                    ),
                ],
            ),
        )

        api_behavior = cloudfront.BehaviorOptions(
            origin=api_cf_origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
            response_headers_policy=security_headers_policy,
        )

        # ----------------------------------------------------------------
        # WAF WebACL (all environments)
        # ----------------------------------------------------------------
        waf_log_group = logs.LogGroup(
            self,
            "WafLogGroup",
            log_group_name=f"aws-waf-logs-channel-{env_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        # WAFv2 needs permission to write to the CloudWatch log group
        waf_log_group.add_to_resource_policy(
            iam.PolicyStatement(
                principals=[iam.ServicePrincipal("delivery.logs.amazonaws.com")],
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"{waf_log_group.log_group_arn}:*"],
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:logs:{self.region}:{self.account}:*"},
                },
            )
        )

        web_acl = wafv2.CfnWebACL(
            self,
            "WebAcl",
            name=f"channel-{env_name}",
            scope="CLOUDFRONT",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=f"channel-{env_name}-waf",
                sampled_requests_enabled=True,
            ),
            rules=[
                # Managed: OWASP Top 10 protections
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesCommonRuleSet",
                    priority=0,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesCommonRuleSet",
                            # GenericRFI_QUERYARGUMENTS blocks query strings
                            # containing `://` patterns (a heuristic for Remote
                            # File Inclusion attacks). The desktop OAuth flow
                            # legitimately passes a
                            #   desktop_callback=http://127.0.0.1:<port>/callback
                            # loopback URL — exactly the pattern this rule blocks.
                            # Without this override, /auth/login with a
                            # desktop_callback returns 403 from the WAF, which
                            # CloudFront's error_responses swap to the SPA
                            # index.html (200 text/html) and the desktop app's
                            # sign-in flow silently breaks.
                            #
                            # Downgrading the single rule to `count` mode keeps
                            # it logging to CloudWatch (so we can spot real
                            # attacks) but stops blocking. RFI protection on
                            # query args is largely redundant with the
                            # application-level input validation in
                            # mgmt_auth._validate_desktop_callback (enforces
                            # loopback host + /callback path + no fragments
                            # or extra query). All other CommonRuleSet rules
                            # (SQLi, XSS, etc.) remain in block mode.
                            rule_action_overrides=[
                                wafv2.CfnWebACL.RuleActionOverrideProperty(
                                    name="GenericRFI_QUERYARGUMENTS",
                                    action_to_use=wafv2.CfnWebACL.RuleActionProperty(
                                        count={},
                                    ),
                                ),
                                # EC2MetaDataSSRF_QUERYARGUMENTS flags query
                                # strings containing the EC2 metadata IP
                                # (169.254.169.254) but ALSO matches localhost
                                # loopback patterns (127.0.0.1:<port>) as
                                # potential SSRF targets. The desktop OAuth
                                # callback `http://127.0.0.1:<port>/callback`
                                # trips this rule too. Same trade-off as
                                # GenericRFI_QUERYARGUMENTS: downgrade to
                                # count so legitimate loopback callbacks pass
                                # while CloudWatch still records matches.
                                wafv2.CfnWebACL.RuleActionOverrideProperty(
                                    name="EC2MetaDataSSRF_QUERYARGUMENTS",
                                    action_to_use=wafv2.CfnWebACL.RuleActionProperty(
                                        count={},
                                    ),
                                ),
                            ],
                        ),
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="AWSManagedRulesCommonRuleSet",
                        sampled_requests_enabled=True,
                    ),
                ),
                # Managed: known malicious input patterns
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesKnownBadInputsRuleSet",
                    priority=1,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesKnownBadInputsRuleSet",
                        ),
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="AWSManagedRulesKnownBadInputsRuleSet",
                        sampled_requests_enabled=True,
                    ),
                ),
                # Rate limit: 1000 req/5min per IP globally
                wafv2.CfnWebACL.RuleProperty(
                    name="GlobalRateLimit",
                    priority=3,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=1000,
                            aggregate_key_type="IP",
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="GlobalRateLimit",
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
        )

        wafv2.CfnLoggingConfiguration(
            self,
            "WafLogging",
            log_destination_configs=[waf_log_group.log_group_arn],
            resource_arn=web_acl.attr_arn,
        )

        web_acl_arn = web_acl.attr_arn

        # CloudFront Function: rewrite clean /docs URLs to the S3 .html files.
        # VitePress with cleanUrls:true outputs flat .html files (e.g.
        # getting-started/quick-start.html), not directory index files.
        # Rules:
        #   /docs or /docs/          → /docs/index.html
        #   /docs/<path>/            → /docs/<path>.html  (strip trailing slash)
        #   /docs/<path> (no ext)    → /docs/<path>.html
        #   /docs/assets/app.js etc  → pass through (has file extension)
        docs_rewrite_fn = cloudfront.Function(
            self,
            "DocsUrlRewrite",
            code=cloudfront.FunctionCode.from_inline(
                """
function handler(event) {
    var request = event.request;
    var uri = request.uri;

    if (!uri.startsWith('/docs')) {
        return request;
    }

    // /docs/app → redirect to /app (Sign in link from docs nav).
    // statusDescription is required; omitting it causes CF to reject the response.
    if (uri === '/docs/app') {
        return {
            statusCode: 302,
            statusDescription: 'Found',
            headers: { location: { value: '/app' } }
        };
    }

    // Last path segment has a dot — treat as a static asset, pass through.
    var lastSegment = uri.split('/').pop();
    if (lastSegment.indexOf('.') !== -1) {
        return request;
    }

    // /docs or /docs/ → serve the docs homepage. Rewrite (not redirect)
    // to /docs/index.html, which VitePress builds from docs-site/index.md.
    // A previous 302 pointed at a getting-started directory path VitePress
    // never produces (no index.html there), so it fell through to the S3
    // 404 → SPA-index fallback and served the marketing app (#230).
    if (uri === '/docs' || uri === '/docs/') {
        request.uri = '/docs/index.html';
        return request;
    }

    // /docs/<path>/ → /docs/<path>.html  (trailing slash, no extension)
    if (uri.endsWith('/')) {
        request.uri = uri.slice(0, -1) + '.html';
        return request;
    }

    // /docs/<path> → /docs/<path>.html
    request.uri = uri + '.html';
    return request;
}
"""
            ),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
        )

        # Single S3 origin shared by default + docs behaviors — two separate
        # with_origin_access_control() calls would create distinct OACs and the
        # second one would not receive a bucket policy grant, causing 403s.
        ui_s3_origin = origins.S3BucketOrigin.with_origin_access_control(ui_bucket)

        docs_behavior = cloudfront.BehaviorOptions(
            origin=ui_s3_origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            response_headers_policy=security_headers_policy,
            function_associations=[
                cloudfront.FunctionAssociation(
                    function=docs_rewrite_fn,
                    event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                )
            ],
        )

        # Cache policy for /updates/*: short TTL for the manifest (latest-mac.yml),
        # long immutable TTL for versioned binaries. Single behavior covers both —
        # per-object Cache-Control headers set by the publish CI job dictate the
        # effective TTL (60s for the manifest, 1y for the .zip + .blockmap).
        updates_cache_policy = cloudfront.CachePolicy(
            self,
            "UpdatesCachePolicy",
            cache_policy_name=f"channel-updates-{env_name}",
            default_ttl=cdk.Duration.seconds(60),
            min_ttl=cdk.Duration.seconds(0),
            max_ttl=cdk.Duration.days(365),
            cookie_behavior=cloudfront.CacheCookieBehavior.none(),
            query_string_behavior=cloudfront.CacheQueryStringBehavior.none(),
            header_behavior=cloudfront.CacheHeaderBehavior.none(),
            enable_accept_encoding_gzip=False,
            enable_accept_encoding_brotli=False,
        )

        updates_behavior = cloudfront.BehaviorOptions(
            origin=ui_s3_origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            cache_policy=updates_cache_policy,
            response_headers_policy=security_headers_policy,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
        )

        distribution = cloudfront.Distribution(
            self,
            "UiDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=ui_s3_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=security_headers_policy,
            ),
            additional_behaviors={
                "/api/*": api_behavior,
                "/auth/*": api_behavior,
                "/health": cloudfront.BehaviorOptions(
                    origin=api_cf_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    response_headers_policy=security_headers_policy,
                ),
                "/docs*": docs_behavior,
                "/updates/*": updates_behavior,
            },
            domain_names=[custom_domain],
            certificate=certificate,
            default_root_object="index.html",
            error_responses=[
                # S3 with OAC returns 403 (not 404) for missing paths.
                # Both must redirect to index.html so React Router handles routing.
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_page_path="/index.html",
                    response_http_status=200,
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_page_path="/index.html",
                    response_http_status=200,
                ),
            ],
        )

        # Associate WAF WebACL with distribution
        cfn_distribution = distribution.node.default_child
        cfn_distribution.add_property_override(  # type: ignore[union-attr]
            "DistributionConfig.WebACLId", web_acl_arn
        )

        # Deploy built UI assets — only if ui/dist exists (built in CI before cdk deploy)
        # prune=False: do not delete objects outside ui/dist (e.g. the docs/ prefix).
        # Without this, CDK's default prune would delete the docs CSS/JS files from S3
        # because they are absent from the React SPA build output, breaking the docs site.
        ui_dist_path = os.path.join(os.path.dirname(__file__), "../../ui/dist")
        deploy_ui = None
        if os.path.exists(ui_dist_path):
            deploy_ui = s3deploy.BucketDeployment(
                self,
                "DeployUi",
                sources=[s3deploy.Source.asset(ui_dist_path)],
                destination_bucket=ui_bucket,
                distribution=distribution,
                distribution_paths=["/*"],
                prune=False,
            )

        # Deploy built docs site assets — only if docs-site/.vitepress/dist exists
        docs_dist_path = os.path.join(os.path.dirname(__file__), "../../docs-site/.vitepress/dist")
        if os.path.exists(docs_dist_path):
            deploy_docs = s3deploy.BucketDeployment(
                self,
                "DeployDocs",
                sources=[s3deploy.Source.asset(docs_dist_path)],
                destination_bucket=ui_bucket,
                destination_key_prefix="docs",
                distribution=distribution,
                distribution_paths=["/docs/*"],
            )
            # Ensure docs are deployed after the UI so that if DeployUi ever
            # re-enables prune the docs files are always the final write.
            if deploy_ui is not None:
                deploy_docs.node.add_dependency(deploy_ui)

        # ----------------------------------------------------------------
        # Route53 alias records — A + AAAA → CloudFront distribution
        # ----------------------------------------------------------------
        cf_alias_target = route53.RecordTarget.from_alias(
            route53_targets.CloudFrontTarget(distribution)
        )
        route53.ARecord(
            self,
            "AliasRecord",
            zone=hosted_zone,
            record_name=issuer_host,
            target=cf_alias_target,
        )
        route53.AaaaRecord(
            self,
            "AliasRecordAAAA",
            zone=hosted_zone,
            record_name=issuer_host,
            target=cf_alias_target,
        )

        # ----------------------------------------------------------------
        # GitHub Actions OIDC deploy role
        # ----------------------------------------------------------------
        # One role per environment, scoped to its GitHub Actions environment.
        # The OIDC provider must already exist in the account (created once via
        # AWS console or: aws iam create-open-id-connect-provider).
        github_oidc = iam.OpenIdConnectProvider.from_open_id_connect_provider_arn(
            self,
            "GitHubOidcProvider",
            f"arn:aws:iam::{self.account}:oidc-provider/token.actions.githubusercontent.com",
        )

        # AdministratorAccess covers s3:PutObject on updates/* and cloudfront:CreateInvalidation for the publish job — no statement-level additions needed.
        deploy_role = iam.Role(
            self,
            "GitHubActionsDeployRole",
            assumed_by=iam.WebIdentityPrincipal(
                github_oidc.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:sub": (
                            f"repo:{GITHUB_REPO}:environment:{github_env}"
                        ),
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    }
                },
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess")
            ],
            description=f"GitHub Actions OIDC deploy role for Channel ({env_name})",
        )

        # ----------------------------------------------------------------
        # CloudWatch log groups — 30-day retention + saved Insights queries
        # ----------------------------------------------------------------

        api_log_group = logs.LogGroup(
            self,
            "ApiLogGroup",
            log_group_name=f"/aws/lambda/{api_fn.function_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=data_removal,
        )

        # Saved CloudWatch Insights queries for operational visibility.
        logs.QueryDefinition(
            self,
            "QueryErrors",
            query_definition_name=f"Channel/{env_name}/errors",
            query_string=logs.QueryString(
                fields=["@timestamp", "client_id", "tool", "error_message"],
                filter_statements=['level = "ERROR"'],
                sort="@timestamp desc",
            ),
            log_groups=[api_log_group],
        )

        logs.QueryDefinition(
            self,
            "QueryTopClients",
            query_definition_name=f"Channel/{env_name}/top-clients",
            query_string=logs.QueryString(
                stats_statements=["count(*) as requests by client_id"],
                sort="requests desc",
            ),
            log_groups=[api_log_group],
        )

        logs.QueryDefinition(
            self,
            "QueryApiLatency",
            query_definition_name=f"Channel/{env_name}/api-latency",
            query_string=logs.QueryString(
                fields=["@timestamp", "method", "path", "status_code", "duration_ms"],
                filter_statements=["ispresent(method)"],
                sort="duration_ms desc",
                limit=100,
            ),
            log_groups=[api_log_group],
        )

        # ----------------------------------------------------------------
        # CloudWatch dashboard + alarms
        # ----------------------------------------------------------------
        dashboard_name = "Channel" if is_prod else f"Channel-{env_name}"

        # SLO targets and derived error budgets
        # MCP availability: 99.5% success → 0.5% error budget
        # API availability: 99.0% success → 1.0% error budget
        # MCP p95 latency: < 2000 ms over 1-hour window
        _MCP_ERROR_BUDGET_PCT = 0.5
        _API_ERROR_BUDGET_PCT = 1.0

        # SNS topic for alarm notifications — prod only gets an email subscription
        # (subscription address lives in SSM /channel/{env}/alarm-email; set it
        # post-deploy, then run `aws sns subscribe --protocol email ...`).
        # On prod that subscription is not optional: the post-deploy gate in
        # ci.yml asserts at least one CONFIRMED subscription on this topic
        # (#537). dev and jc are accepted as silent — see the
        # "Post-deploy assertion" section above ``class ChannelStack``.
        alarm_topic = sns.Topic(
            self,
            "AlarmTopic",
            display_name=f"Channel alarms ({env_name})",
        )

        def _notify(alarm: cw.Alarm) -> cw.Alarm:
            """Attach SNS alarm + OK actions (prod only), so recovery also pages."""
            if is_prod:
                action = cw_actions.SnsAction(alarm_topic)
                alarm.add_alarm_action(action)
                alarm.add_ok_action(action)
            return alarm

        def _error_rate_alarm(
            construct_id: str,
            fn: lambda_.Function,
            label: str,
        ) -> cw.Alarm:
            """Lambda error rate alarm: > 5% over two consecutive 5-min periods."""
            errors = fn.metric_errors(period=cdk.Duration.minutes(5), statistic="Sum")
            invocations = fn.metric_invocations(period=cdk.Duration.minutes(5), statistic="Sum")
            error_rate = cw.MathExpression(
                expression="100 * errors / MAX([errors, invocations])",
                using_metrics={"errors": errors, "invocations": invocations},
                label=f"{label} error rate %",
                period=cdk.Duration.minutes(5),
            )
            alarm = cw.Alarm(
                self,
                construct_id,
                alarm_name=f"Channel-{env_name}-{construct_id.removesuffix('Alarm')}",
                metric=error_rate,
                threshold=5,
                evaluation_periods=2,
                datapoints_to_alarm=2,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description=f"Channel {label} error rate > 5% ({env_name})",
            )
            return _notify(alarm)

        api_error_alarm = _error_rate_alarm("ApiErrorRateAlarm", api_fn, "API")

        # DynamoDB throttle alarm: any throttled requests over 5 min
        ddb_throttle_alarm = cw.Alarm(
            self,
            "DdbThrottleAlarm",
            alarm_name=f"Channel-{env_name}-DdbThrottles",
            metric=cw.Metric(
                namespace="AWS/DynamoDB",
                metric_name="ThrottledRequests",
                dimensions_map={"TableName": table.table_name},
                period=cdk.Duration.minutes(5),
                statistic="Sum",
            ),
            threshold=0,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Channel DynamoDB throttled requests > 0 ({env_name})",
        )
        _notify(ddb_throttle_alarm)

        # CloudFront 5xx error rate alarm: > 1% over 5 min
        cf_5xx_alarm = cw.Alarm(
            self,
            "CloudFront5xxAlarm",
            alarm_name=f"Channel-{env_name}-CloudFront5xx",
            metric=cw.Metric(
                namespace="AWS/CloudFront",
                metric_name="5xxErrorRate",
                dimensions_map={
                    "DistributionId": distribution.distribution_id,
                    "Region": "Global",
                },
                period=cdk.Duration.minutes(5),
                statistic="Average",
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Channel CloudFront 5xx rate > 1% ({env_name})",
        )
        _notify(cf_5xx_alarm)

        # Custom EMF metric alarms
        #
        # #111 — three alarms in this block used to reference metric names
        # that nothing in ``src/channel`` ever emitted (``ToolErrors``,
        # ``StorageLatencyMs``, ``TokenValidationFailures``). With
        # ``treat_missing_data=NOT_BREACHING`` they sat permanently green,
        # advertising coverage that did not exist. ``ToolErrors`` is
        # re-pointed at the counter that IS emitted; the other two are
        # removed rather than left as decoration. Reviving an auth-failure
        # counter needs a sync-safe emit path in ``api/_auth.py`` (the
        # dependency is not a coroutine) — tracked as a follow-up, not
        # papered over here.
        def _channel_metric(
            metric_name: str,
            *,
            statistic: str = "Sum",
            period_minutes: int = 5,
        ) -> cw.Metric:
            """A ``Channel``-namespace EMF metric on this env's aggregate
            dimension set.

            ``{"Environment": env_name}`` is exactly the base dimension set
            ``channel.metrics.emit_metric`` writes, and CloudWatch matches a
            metric only on an exact dimension set — so the per-``Route``
            breakdown series (#111) is deliberately NOT selected here.
            Alarms fire on the service-wide aggregate; per-route drill-down
            is a dashboard/Insights activity, not an alarm.
            """
            return cw.Metric(
                namespace="Channel",
                metric_name=metric_name,
                dimensions_map={"Environment": env_name},
                period=cdk.Duration.minutes(period_minutes),
                statistic=statistic,
            )

        tool_errors_alarm = cw.Alarm(
            self,
            "ToolErrorsAlarm",
            alarm_name=f"Channel-{env_name}-ToolErrors",
            metric=_channel_metric("ToolCallFailures"),
            threshold=10,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Channel tool call failures > 10 in 5 min ({env_name})",
        )
        _notify(tool_errors_alarm)

        # ----------------------------------------------------------------
        # Request-level SLIs (#111) — emitted by the api.main middleware
        # ----------------------------------------------------------------
        # Lambda's own ``Errors`` metric (the ApiErrorRateAlarm above) only
        # counts invocations that raised out of the handler. A FastAPI route
        # returning HTTP 500 is a *successful* Lambda invocation, so the
        # whole application-error surface was previously unalarmed. These
        # alarms close that gap.
        def _request_error_rate(window_minutes: int) -> cw.MathExpression:
            """5xx responses as a percentage of all responses.

            ``MAX([errors, requests])`` is the same zero-denominator guard
            the Lambda error-rate helper uses: ``Request5xxCount`` is only
            emitted when a 5xx actually happens, so the two series can be
            sparse independently and a naive division would produce
            ``Infinity`` in the window where errors arrive first.
            """
            errors = _channel_metric("Request5xxCount", period_minutes=window_minutes)
            requests = _channel_metric("RequestCount", period_minutes=window_minutes)
            return cw.MathExpression(
                expression="100 * errors / MAX([errors, requests])",
                using_metrics={"errors": errors, "requests": requests},
                label="API 5xx rate %",
                period=cdk.Duration.minutes(window_minutes),
            )

        api_request_error_alarm = cw.Alarm(
            self,
            "ApiRequestErrorRateAlarm",
            alarm_name=f"Channel-{env_name}-ApiRequestErrorRate",
            metric=_request_error_rate(5),
            threshold=5,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Channel API 5xx response rate > 5% ({env_name})",
        )
        _notify(api_request_error_alarm)

        # p99 time-to-response-start. The middleware measures the
        # ``call_next`` window, which for an SSE turn closes when the
        # headers go out — a streaming chat contributes its first-byte
        # latency, not its multi-minute stream duration. That is what makes
        # a single service-wide p99 threshold meaningful here.
        api_request_latency_alarm = cw.Alarm(
            self,
            "ApiRequestLatencyAlarm",
            alarm_name=f"Channel-{env_name}-ApiRequestLatencyHigh",
            metric=_channel_metric("RequestLatencyMs", statistic="p99"),
            threshold=3000,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Channel API request latency p99 > 3000ms ({env_name})",
        )
        _notify(api_request_latency_alarm)

        # ----------------------------------------------------------------
        # Bedrock SLIs (#111) — emitted per streamed chat turn
        # ----------------------------------------------------------------
        # ``BedrockLatencyMs`` gets exactly one datapoint per turn, so its
        # SampleCount IS the turn count — used as the error-rate denominator
        # rather than paying for a separate ``BedrockTurns`` counter.
        bedrock_turns = _channel_metric("BedrockLatencyMs", statistic="SampleCount")
        bedrock_errors = _channel_metric("BedrockErrors")
        bedrock_error_rate_alarm = cw.Alarm(
            self,
            "BedrockErrorRateAlarm",
            alarm_name=f"Channel-{env_name}-BedrockErrorRate",
            metric=cw.MathExpression(
                expression="100 * errors / MAX([errors, turns])",
                using_metrics={"errors": bedrock_errors, "turns": bedrock_turns},
                label="Bedrock turn error rate %",
                period=cdk.Duration.minutes(5),
            ),
            threshold=10,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Channel Bedrock turn error rate > 10% ({env_name})",
        )
        _notify(bedrock_error_rate_alarm)

        # Throttling is a quota wall, not a bug: the response is to switch
        # model or raise the account quota, so it gets its own alarm rather
        # than hiding inside the error rate. Two consecutive breaching 5-min
        # periods, so a single retried burst stays quiet.
        bedrock_throttle_alarm = cw.Alarm(
            self,
            "BedrockThrottleAlarm",
            alarm_name=f"Channel-{env_name}-BedrockThrottles",
            metric=_channel_metric("BedrockThrottles"),
            threshold=0,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=(
                f"Channel Bedrock throttling in 2 consecutive 5-min periods ({env_name})"
            ),
        )
        _notify(bedrock_throttle_alarm)

        # ----------------------------------------------------------------
        # Refresh-token breach signal (#294 emits it, #496 watches it)
        # ----------------------------------------------------------------
        # ``RefreshReuseDetected`` is the RFC 9700 §4.14.2 signal: a
        # rotated refresh token was presented a second time, so
        # ``consume_refresh_token`` revoked the whole device family. In
        # practice a replayed refresh credential means a stolen one. #290
        # already responds automatically and records it; this alarm is the
        # only thing that tells a *human* it happened.
        #
        # The thresholds differ from every other alarm in this file on
        # purpose. The rest watch **rates**, because one 5xx or one
        # throttle is noise and only a sustained level means anything. This
        # one is a **breach indicator**: a single occurrence is the whole
        # event. ``threshold=0`` with ``GREATER_THAN`` is "at least one in
        # a 5-minute period", and ``evaluation_periods=1`` /
        # ``datapoints_to_alarm=1`` refuse to wait for a second one. There
        # is no sensitivity to tune here — contrast ``BedrockThrottles``
        # directly above, which deliberately wants two consecutive periods.
        #
        # ``NOT_BREACHING`` is correct here for the same reason it was
        # wrong on the three alarms it sank in #111. There it masked
        # metrics that nothing could ever emit, so permanent green
        # advertised coverage that did not exist. Here the metric is sparse
        # **by design** — a period with no datapoint means no breach, which
        # is genuinely not-breaching — and both halves of the chain are
        # pinned by tests rather than asserted in prose:
        # ``channel.metrics.record_refresh_outcome`` publishes the name
        # (``test_every_channel_namespace_alarm_references_an_emitted_metric``)
        # and ``POST /auth/refresh`` reaches that branch
        # (``tests/unit/test_auth_refresh.py``). Both alternatives are
        # worse: ``BREACHING`` would sit in ALARM permanently on an idle
        # service, and ``MISSING`` parks the alarm in INSUFFICIENT_DATA
        # between events — visually indistinguishable from a broken alarm,
        # which is the perception failure this whole family is about.
        # NOT_BREACHING also returns the alarm to OK after an incident, so
        # a *second* theft re-notifies instead of being swallowed by an
        # already-latched ALARM state.
        refresh_reuse_alarm = cw.Alarm(
            self,
            "RefreshReuseDetectedAlarm",
            alarm_name=f"Channel-{env_name}-RefreshReuseDetected",
            metric=_channel_metric("RefreshReuseDetected"),
            threshold=0,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=(
                f"Channel refresh-token reuse detected ({env_name}) — a rotated refresh "
                "token was replayed and its whole device family was revoked (RFC 9700 "
                "4.14.2). Treat as credential theft and investigate; this is a security "
                "signal, not a health metric, so any non-zero value is the event."
            ),
        )
        _notify(refresh_reuse_alarm)

        # Lambda throttles — any throttled invocation is a capacity issue to
        # investigate immediately; no tolerance.
        def _throttle_alarm(construct_id: str, fn: lambda_.Function, label: str) -> cw.Alarm:
            alarm = cw.Alarm(
                self,
                construct_id,
                alarm_name=f"Channel-{env_name}-{construct_id.removesuffix('Alarm')}",
                metric=fn.metric_throttles(period=cdk.Duration.minutes(5), statistic="Sum"),
                threshold=0,
                evaluation_periods=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description=f"Channel {label} Lambda throttles > 0 ({env_name})",
            )
            return _notify(alarm)

        _throttle_alarm("ApiThrottlesAlarm", api_fn, "API")

        # DynamoDB user errors — 4xx-class failures from the SDK (validation,
        # ConditionalCheckFailed, etc.). A small rate is normal (optimistic
        # writes race); > 10 in 5 min usually means a bug or a misconfigured
        # client.
        ddb_user_errors_alarm = cw.Alarm(
            self,
            "DdbUserErrorsAlarm",
            alarm_name=f"Channel-{env_name}-DdbUserErrors",
            metric=cw.Metric(
                namespace="AWS/DynamoDB",
                metric_name="UserErrors",
                dimensions_map={"TableName": table.table_name},
                period=cdk.Duration.minutes(5),
                statistic="Sum",
            ),
            threshold=10,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Channel DynamoDB user errors > 10 in 5 min ({env_name})",
        )
        _notify(ddb_user_errors_alarm)

        # SLO burn rate alarms
        # Fast burn (>5×): error rate exceeds 5 × error_budget over 1 hour
        # If MCP error budget = 0.5%, fast-burn threshold = 2.5%
        # Slow burn (>2×): error rate exceeds 2 × error_budget over 6 hours
        def _burn_rate_alarm(
            construct_id: str,
            fn: lambda_.Function,
            label: str,
            error_budget_pct: float,
            burn_multiplier: float,
            window_minutes: int,
        ) -> cw.Alarm:
            """Burn rate alarm using error rate vs SLO error budget."""
            errors = fn.metric_errors(period=cdk.Duration.minutes(window_minutes), statistic="Sum")
            invocations = fn.metric_invocations(
                period=cdk.Duration.minutes(window_minutes), statistic="Sum"
            )
            error_rate_pct = cw.MathExpression(
                expression="100 * errors / MAX([errors, invocations])",
                using_metrics={"errors": errors, "invocations": invocations},
                label=f"{label} error rate %",
                period=cdk.Duration.minutes(window_minutes),
            )
            threshold = burn_multiplier * error_budget_pct
            burn_type = "fast" if burn_multiplier >= 5 else "slow"
            alarm = cw.Alarm(
                self,
                construct_id,
                alarm_name=f"Channel-{env_name}-{construct_id.removesuffix('Alarm')}",
                metric=error_rate_pct,
                threshold=threshold,
                evaluation_periods=1,
                datapoints_to_alarm=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description=(
                    f"Channel {label} SLO {burn_type}-burn: error rate > {threshold}% "
                    f"over {window_minutes}m (budget={error_budget_pct}% × {burn_multiplier}×) ({env_name})"
                ),
            )
            return _notify(alarm)

        _burn_rate_alarm("ApiFastBurnAlarm", api_fn, "API", _API_ERROR_BUDGET_PCT, 5, 60)
        _burn_rate_alarm("ApiSlowBurnAlarm", api_fn, "API", _API_ERROR_BUDGET_PCT, 2, 360)

        # Request-level burn rate (#111). Same 1h / 6h multi-window shape as
        # the Lambda-metric pair above, but measured on the SLI users
        # actually experience: HTTP 5xx responses. The issue's definition of
        # done — "an error-rate burn alarm fires within 1h of a sustained
        # 5xx spike" — is this fast-burn alarm; the Lambda-metric one above
        # cannot see a handled 500 at all.
        def _request_burn_rate_alarm(
            construct_id: str,
            burn_multiplier: float,
            window_minutes: int,
        ) -> cw.Alarm:
            threshold = burn_multiplier * _API_ERROR_BUDGET_PCT
            burn_type = "fast" if burn_multiplier >= 5 else "slow"
            alarm = cw.Alarm(
                self,
                construct_id,
                alarm_name=f"Channel-{env_name}-{construct_id.removesuffix('Alarm')}",
                metric=_request_error_rate(window_minutes),
                threshold=threshold,
                evaluation_periods=1,
                datapoints_to_alarm=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description=(
                    f"Channel API request SLO {burn_type}-burn: 5xx rate > {threshold}% "
                    f"over {window_minutes}m "
                    f"(budget={_API_ERROR_BUDGET_PCT}% x {burn_multiplier}x) ({env_name})"
                ),
            )
            return _notify(alarm)

        _request_burn_rate_alarm("ApiRequestFastBurnAlarm", 5, 60)
        _request_burn_rate_alarm("ApiRequestSlowBurnAlarm", 2, 360)

        # Dashboard
        dashboard = cw.Dashboard(
            self,
            "StarterDashboard",
            dashboard_name=dashboard_name,
        )

        dashboard.add_widgets(
            cw.Row(
                cw.TextWidget(
                    markdown=f"# Channel — {env_name}  \nLambda · DynamoDB · CloudFront",
                    width=24,
                    height=1,
                ),
            ),
            # API Lambda row
            cw.Row(
                cw.TextWidget(markdown="## API Lambda", width=24, height=1),
            ),
            cw.Row(
                cw.GraphWidget(
                    title="API Invocations & Errors",
                    left=[
                        api_fn.metric_invocations(period=cdk.Duration.minutes(5), statistic="Sum")
                    ],
                    right=[api_fn.metric_errors(period=cdk.Duration.minutes(5), statistic="Sum")],
                    width=8,
                ),
                cw.GraphWidget(
                    title="API Duration (ms)",
                    left=[
                        api_fn.metric_duration(period=cdk.Duration.minutes(5), statistic="p50"),
                        api_fn.metric_duration(period=cdk.Duration.minutes(5), statistic="p95"),
                        api_fn.metric_duration(period=cdk.Duration.minutes(5), statistic="p99"),
                    ],
                    width=8,
                ),
                cw.GraphWidget(
                    title="API Throttles",
                    left=[api_fn.metric_throttles(period=cdk.Duration.minutes(5), statistic="Sum")],
                    width=8,
                ),
            ),
            # DynamoDB row
            cw.Row(
                cw.TextWidget(markdown="## DynamoDB", width=24, height=1),
            ),
            cw.Row(
                cw.GraphWidget(
                    title="DDB Read/Write Capacity",
                    left=[
                        cw.Metric(
                            namespace="AWS/DynamoDB",
                            metric_name="ConsumedReadCapacityUnits",
                            dimensions_map={"TableName": table.table_name},
                            period=cdk.Duration.minutes(5),
                            statistic="Sum",
                        ),
                        cw.Metric(
                            namespace="AWS/DynamoDB",
                            metric_name="ConsumedWriteCapacityUnits",
                            dimensions_map={"TableName": table.table_name},
                            period=cdk.Duration.minutes(5),
                            statistic="Sum",
                        ),
                    ],
                    width=8,
                ),
                cw.GraphWidget(
                    title="DDB Throttled Requests",
                    left=[
                        cw.Metric(
                            namespace="AWS/DynamoDB",
                            metric_name="ThrottledRequests",
                            dimensions_map={"TableName": table.table_name},
                            period=cdk.Duration.minutes(5),
                            statistic="Sum",
                        )
                    ],
                    width=8,
                ),
                cw.GraphWidget(
                    title="DDB System Errors",
                    left=[
                        cw.Metric(
                            namespace="AWS/DynamoDB",
                            metric_name="SystemErrors",
                            dimensions_map={"TableName": table.table_name},
                            period=cdk.Duration.minutes(5),
                            statistic="Sum",
                        )
                    ],
                    width=8,
                ),
            ),
            # CloudFront row
            cw.Row(
                cw.TextWidget(markdown="## CloudFront", width=24, height=1),
            ),
            cw.Row(
                cw.GraphWidget(
                    title="CF Requests",
                    left=[
                        cw.Metric(
                            namespace="AWS/CloudFront",
                            metric_name="Requests",
                            dimensions_map={
                                "DistributionId": distribution.distribution_id,
                                "Region": "Global",
                            },
                            period=cdk.Duration.minutes(5),
                            statistic="Sum",
                        )
                    ],
                    width=6,
                ),
                cw.GraphWidget(
                    title="CF Cache Hit Rate %",
                    left=[
                        cw.Metric(
                            namespace="AWS/CloudFront",
                            metric_name="CacheHitRate",
                            dimensions_map={
                                "DistributionId": distribution.distribution_id,
                                "Region": "Global",
                            },
                            period=cdk.Duration.minutes(5),
                            statistic="Average",
                        )
                    ],
                    width=6,
                ),
                cw.GraphWidget(
                    title="CF 4xx / 5xx Error Rate %",
                    left=[
                        cw.Metric(
                            namespace="AWS/CloudFront",
                            metric_name="4xxErrorRate",
                            dimensions_map={
                                "DistributionId": distribution.distribution_id,
                                "Region": "Global",
                            },
                            period=cdk.Duration.minutes(5),
                            statistic="Average",
                        ),
                        cw.Metric(
                            namespace="AWS/CloudFront",
                            metric_name="5xxErrorRate",
                            dimensions_map={
                                "DistributionId": distribution.distribution_id,
                                "Region": "Global",
                            },
                            period=cdk.Duration.minutes(5),
                            statistic="Average",
                        ),
                    ],
                    width=6,
                ),
                cw.GraphWidget(
                    title="CF Origin Latency (ms)",
                    left=[
                        cw.Metric(
                            namespace="AWS/CloudFront",
                            metric_name="OriginLatency",
                            dimensions_map={
                                "DistributionId": distribution.distribution_id,
                                "Region": "Global",
                            },
                            period=cdk.Duration.minutes(5),
                            statistic="p99",
                        )
                    ],
                    width=6,
                ),
            ),
            # Request SLIs row (#111). Replaces the three widgets that
            # graphed `ToolInvocations` / `ToolErrors` /
            # `TokenValidationFailures` — metric names nothing ever emitted,
            # so those panels were permanently blank.
            cw.Row(
                cw.TextWidget(markdown="## Requests (application)", width=24, height=1),
            ),
            cw.Row(
                cw.GraphWidget(
                    title="Requests & Errors",
                    left=[_channel_metric("RequestCount")],
                    right=[
                        _channel_metric("Request4xxCount"),
                        _channel_metric("Request5xxCount"),
                    ],
                    width=8,
                ),
                cw.GraphWidget(
                    title="Request Latency to First Byte (ms)",
                    left=[
                        _channel_metric("RequestLatencyMs", statistic="p50"),
                        _channel_metric("RequestLatencyMs", statistic="p95"),
                        _channel_metric("RequestLatencyMs", statistic="p99"),
                    ],
                    width=8,
                ),
                cw.GraphWidget(
                    title="Tool Call Failures",
                    left=[
                        _channel_metric("ToolCallSuccesses"),
                        _channel_metric("ToolCallFailures"),
                    ],
                    width=8,
                ),
            ),
            # Bedrock row (#111)
            cw.Row(
                cw.TextWidget(markdown="## Bedrock", width=24, height=1),
            ),
            cw.Row(
                cw.GraphWidget(
                    title="Bedrock Turn Latency (ms)",
                    left=[
                        _channel_metric("BedrockLatencyMs", statistic="p50"),
                        _channel_metric("BedrockLatencyMs", statistic="p95"),
                        _channel_metric("BedrockLatencyMs", statistic="p99"),
                    ],
                    width=8,
                ),
                cw.GraphWidget(
                    title="Bedrock Tokens",
                    left=[
                        _channel_metric("BedrockTokensIn"),
                        _channel_metric("BedrockTokensOut"),
                    ],
                    width=8,
                ),
                cw.GraphWidget(
                    title="Bedrock Errors & Throttles",
                    left=[
                        _channel_metric("BedrockErrors"),
                        _channel_metric("BedrockThrottles"),
                    ],
                    right=[bedrock_turns],
                    width=8,
                ),
            ),
            # Alarms row
            cw.Row(
                cw.TextWidget(markdown="## Alarms", width=24, height=1),
            ),
            cw.Row(
                cw.AlarmWidget(alarm=api_error_alarm, title="API Error Rate", width=6),
                cw.AlarmWidget(alarm=ddb_throttle_alarm, title="DDB Throttles", width=6),
                cw.AlarmWidget(alarm=api_request_error_alarm, title="API 5xx Rate", width=6),
                cw.AlarmWidget(alarm=api_request_latency_alarm, title="API Latency p99", width=6),
            ),
            cw.Row(
                cw.AlarmWidget(alarm=bedrock_error_rate_alarm, title="Bedrock Error Rate", width=6),
                cw.AlarmWidget(alarm=bedrock_throttle_alarm, title="Bedrock Throttles", width=6),
            ),
        )

        # ----------------------------------------------------------------
        # Outputs
        # ----------------------------------------------------------------
        cdk.CfnOutput(
            self, "ApiFunctionUrl", value=api_url.url, description="API Lambda URL (direct)"
        )
        cdk.CfnOutput(self, "TableName", value=table.table_name, description="DynamoDB table name")
        cdk.CfnOutput(
            self,
            "UiUrl",
            value=f"https://{custom_domain}",
            description="Management UI URL",
        )
        cdk.CfnOutput(
            self,
            "DeployRoleArn",
            value=deploy_role.role_arn,
            description=f"GitHub Actions OIDC deploy role ARN ({env_name})",
        )
        cdk.CfnOutput(
            self,
            "WebAclArn",
            value=web_acl_arn,
            description=f"WAFv2 WebACL ARN ({env_name})",
        )
        cdk.CfnOutput(
            self,
            "AppVersion",
            value=app_version,
            description="Deployed application version",
        )
        cdk.CfnOutput(
            self,
            ALARM_TOPIC_ARN_OUTPUT,
            value=alarm_topic.topic_arn,
            description=(
                f"SNS topic every CloudWatch alarm publishes to ({env_name}). "
                "Read by the prod confirmed-subscription gate in ci.yml (#537)."
            ),
        )
        cdk.CfnOutput(
            self,
            "DashboardUrl",
            value=f"https://{self.region}.console.aws.amazon.com/cloudwatch/home#dashboards:name={dashboard_name}",
            description="CloudWatch dashboard URL",
        )
        cdk.CfnOutput(
            self,
            "UpdatesFeedUrl",
            value=f"https://{custom_domain}/updates",
            description="Base URL for the desktop auto-update manifest tree (channel suffix appended at build time)",
        )
        cdk.CfnOutput(
            self,
            "UpdatesBucketName",
            value=ui_bucket.bucket_name,
            description="S3 bucket name for the `aws s3 sync` step in publish-desktop-mac",
        )
        cdk.CfnOutput(
            self,
            "UpdatesDistributionId",
            value=distribution.distribution_id,
            description="CloudFront distribution ID for the cache invalidation step",
        )

        # ----------------------------------------------------------------
        # cdk-nag suppressions
        # ----------------------------------------------------------------
        NagSuppressions.add_stack_suppressions(
            self,
            [
                # AWSLambdaBasicExecutionRole is the standard minimal Lambda
                # execution role recommended by AWS. Using a more restrictive
                # custom policy would require duplicating its managed policy
                # contents, adding maintenance burden with no security benefit.
                NagPackSuppression(
                    id="AwsSolutions-IAM4",
                    reason="AWSLambdaBasicExecutionRole is the standard least-privilege Lambda execution role.",
                ),
                # CloudWatch GetMetricData and Cost Explorer GetCostAndUsage
                # do not support resource-level permissions — AWS requires '*'.
                # The WAF log delivery condition ARN also requires a wildcard
                # resource in the resource policy. All DynamoDB grants use
                # table-scoped ARNs; the '*' finding applies only to the above.
                NagPackSuppression(
                    id="AwsSolutions-IAM5",
                    reason="CloudWatch GetMetricData and ce:GetCostAndUsage require resource '*' per AWS docs. WAF log delivery policy requires wildcard resource condition.",
                ),
                # PYTHON_3_12 is the latest stable Lambda runtime available
                # in aws-cdk-lib at the time of writing. We track the latest
                # available runtime and will upgrade when 3.13 is GA in CDK.
                NagPackSuppression(
                    id="AwsSolutions-L1",
                    reason="PYTHON_3_12 is the latest stable Lambda runtime available in CDK. Will upgrade to 3.13 when available.",
                ),
                # S3 server-access logging would write to another S3 bucket,
                # creating a circular dependency and cost. CloudFront access
                # logs (via CloudWatch metrics) provide sufficient visibility
                # into access patterns for this SaaS product.
                NagPackSuppression(
                    id="AwsSolutions-S1",
                    reason="CloudFront metrics provide sufficient access visibility. S3 server-access logging adds cost and bucket management overhead.",
                ),
                # CloudFront access logging is expensive and produces high
                # volumes of data. We use CloudWatch metrics (via EMF) and
                # CloudWatch alarms for operational visibility instead.
                NagPackSuppression(
                    id="AwsSolutions-CFR3",
                    reason="CloudWatch metrics and alarms provide operational visibility. CloudFront access logging not required for this use case.",
                ),
                # Geo-restriction is intentionally not applied — Channel
                # is available to users worldwide.
                NagPackSuppression(
                    id="AwsSolutions-CFR1",
                    reason="Channel is a globally available service. Geo-restriction is not appropriate.",
                ),
                # Lambda Function URLs are used instead of API Gateway.
                # They are public by design — the origin-verify secret and
                # JWT auth in the application layer enforce access control.
                NagPackSuppression(
                    id="AwsSolutions-FAS1",
                    reason="Function URL auth=NONE is intentional; origin-verify header + JWT auth in the application layer enforce access control.",
                ),
                # SNS topic encryption with KMS would add per-message costs
                # for alarm notifications. The topic carries no sensitive
                # payload — only alarm state change notifications.
                NagPackSuppression(
                    id="AwsSolutions-SNS2",
                    reason="SNS topic carries only CloudWatch alarm notifications (no sensitive data). KMS encryption adds cost without meaningful security benefit.",
                ),
                # Enforcing SSL-only on the alarm SNS topic would require a
                # resource policy that restricts all AWS services, which can
                # break CloudWatch alarm delivery in some regions.
                NagPackSuppression(
                    id="AwsSolutions-SNS3",
                    reason="SSL-only policy on alarm SNS topic can break CloudWatch alarm delivery. Alarms carry no sensitive data.",
                ),
            ],
        )


# Entry point for the prod post-deploy alarm-subscription gate (#537).
# The implementation lives just above ``class ChannelStack`` — see the
# "Post-deploy assertion" section there.
if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
