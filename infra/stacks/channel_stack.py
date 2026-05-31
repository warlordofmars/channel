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

import os

import aws_cdk as cdk
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
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

        # ----------------------------------------------------------------
        # SSM Parameters
        # ----------------------------------------------------------------
        # All parameters use per-environment paths to prevent secret sharing.
        # Prod keeps legacy paths (no env suffix) for backward compatibility.
        def _ssm_path(name: str) -> str:
            return (
                f"/channel/{name}" if is_prod else f"/channel/{env_name}/{name}"
            )

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
            "..",
            bundling=cdk.BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                command=[
                    "bash",
                    "-c",
                    " && ".join(
                        [
                            "pip install uv --quiet --no-cache-dir",
                            # Export only runtime deps — exclude dev and infra (CDK) groups
                            "UV_CACHE_DIR=/tmp/uv-cache uv export --no-hashes --no-group dev --no-group infra -o /tmp/requirements.txt",
                            "pip install -r /tmp/requirements.txt -t /asset-output --quiet --no-cache-dir",
                            "cp -r src/channel /asset-output/channel",
                            # run.sh is the AWSLWA entrypoint — must be executable at Lambda root
                            "cp run.sh /asset-output/run.sh",
                            "chmod +x /asset-output/run.sh",
                        ]
                    ),
                ],
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
            "STARTER_TABLE_NAME": table.table_name,
            # Custom domain is the canonical issuer URL for all environments.
            "STARTER_ISSUER": f"https://{custom_domain}",
            # Tell both Lambdas which SSM parameter holds the JWT secret.
            "STARTER_JWT_SECRET_PARAM": ssm_param_name,
            # Google OAuth 2.0 SSM parameter paths
            "GOOGLE_CLIENT_ID_PARAM": google_client_id_param.parameter_name,
            "GOOGLE_CLIENT_SECRET_PARAM": google_client_secret_param.parameter_name,
            "ALLOWED_EMAILS_PARAM": allowed_emails_param.parameter_name,
            "STARTER_ORIGIN_VERIFY_PARAM": origin_verify_param.parameter_name,
            # Operational SSM parameter — soft-warn check at startup logs if
            # still set to the CHANGE_ME_ON_FIRST_DEPLOY placeholder.
            "STARTER_ALARM_EMAIL_PARAM": alarm_email_param.parameter_name,
            # APP_VERSION is injected at deploy time via the APP_VERSION env var.
            # Falls back to "dev" for local synth/deploy without a version set.
            "APP_VERSION": app_version,
            # Used by EMF metrics as the "Environment" dimension.
            "STARTER_ENV": env_name,
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
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:DeleteEvent",
                    "bedrock-agentcore:CreateMemory",
                    "bedrock-agentcore:GetMemory",
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
            common_env["STARTER_ENABLE_DEBUG_ENDPOINTS"] = "1"

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
            self.node.try_get_context("origin_verify_secret")
            or "CHANGE_ME_ON_FIRST_DEPLOY"
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
        # CSP ships in Report-Only mode first; browser console surfaces
        # violations without breaking the site. Flip to enforcing in a
        # follow-up once logs are clean for ~1 week.
        # ----------------------------------------------------------------
        # Violations POST to /api/csp-report on the same origin; the endpoint
        # is unauthenticated + per-IP rate-limited. `report-uri` is the legacy
        # directive; `report-to default` targets the modern Reporting API.
        csp_report_only = (
            "default-src 'self'; "
            "script-src 'self' https://www.googletagmanager.com; "
            "connect-src 'self' https://www.google-analytics.com "
            "https://channel.example.com; "
            "img-src 'self' data: https://www.google-analytics.com; "
            "style-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "report-uri /api/csp-report; "
            "report-to default;"
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

    // /docs or /docs/ → redirect to the first doc page
    if (uri === '/docs' || uri === '/docs/') {
        return {
            statusCode: 302,
            statusDescription: 'Found',
            headers: { location: { value: '/docs/getting-started/' } }
        };
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
        tool_errors_alarm = cw.Alarm(
            self,
            "ToolErrorsAlarm",
            alarm_name=f"Channel-{env_name}-ToolErrors",
            metric=cw.Metric(
                namespace="Channel",
                metric_name="ToolErrors",
                dimensions_map={"Environment": env_name},
                period=cdk.Duration.minutes(5),
                statistic="Sum",
            ),
            threshold=10,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Channel tool errors > 10 in 5 min ({env_name})",
        )
        _notify(tool_errors_alarm)

        storage_latency_alarm = cw.Alarm(
            self,
            "StorageLatencyAlarm",
            alarm_name=f"Channel-{env_name}-StorageLatencyHigh",
            metric=cw.Metric(
                namespace="Channel",
                metric_name="StorageLatencyMs",
                dimensions_map={"Environment": env_name},
                period=cdk.Duration.minutes(5),
                statistic="p99",
            ),
            threshold=2000,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Channel storage latency p99 > 2000ms ({env_name})",
        )
        _notify(storage_latency_alarm)

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

        # Business metric: Bearer-token rejections from the existing
        # `TokenValidationFailures` EMF metric. Spike usually means a
        # credential leak or a misconfigured client — investigate before it
        # triggers rate-limiter churn.
        auth_failures_alarm = cw.Alarm(
            self,
            "AuthFailuresAlarm",
            alarm_name=f"Channel-{env_name}-AuthFailures",
            metric=cw.Metric(
                namespace="Channel",
                metric_name="TokenValidationFailures",
                dimensions_map={"Environment": env_name},
                period=cdk.Duration.minutes(5),
                statistic="Sum",
            ),
            threshold=10,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Channel auth failures > 10 in 5 min ({env_name})",
        )
        _notify(auth_failures_alarm)

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
            cw.Row(
                cw.GraphWidget(
                    title="Tool Invocations",
                    left=[
                        cw.Metric(
                            namespace="Channel",
                            metric_name="ToolInvocations",
                            dimensions_map={"Environment": env_name},
                            period=cdk.Duration.minutes(5),
                            statistic="Sum",
                        )
                    ],
                    width=8,
                ),
                cw.GraphWidget(
                    title="Tool Errors",
                    left=[
                        cw.Metric(
                            namespace="Channel",
                            metric_name="ToolErrors",
                            dimensions_map={"Environment": env_name},
                            period=cdk.Duration.minutes(5),
                            statistic="Sum",
                        )
                    ],
                    width=8,
                ),
                cw.GraphWidget(
                    title="Token Validation Failures",
                    left=[
                        cw.Metric(
                            namespace="Channel",
                            metric_name="TokenValidationFailures",
                            dimensions_map={"Environment": env_name},
                            period=cdk.Duration.minutes(5),
                            statistic="Sum",
                        )
                    ],
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
