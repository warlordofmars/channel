#!/usr/bin/env python3
# Copyright (c) 2026 John Carter. All rights reserved.
"""CDK app entry point for Channel infrastructure."""

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks
from stacks.channel_stack import ChannelStack

app = cdk.App()

# env_name is passed at deploy time: cdk deploy -c env=dev
# Defaults to "prod" if not provided.
env_name = app.node.try_get_context("env") or "prod"
stack_id = "ChannelStack" if env_name == "prod" else f"ChannelStack-{env_name}"

env = cdk.Environment(
    # account is passed at deploy time: cdk deploy -c account=123456789012
    # Falls back to the CloudFormation pseudo-parameter for synth without credentials.
    account=app.node.try_get_context("account") or cdk.Aws.ACCOUNT_ID,
    region=app.node.try_get_context("region") or "us-east-1",
)

# hosted_zone_id is required for Route53 hosted zone lookup without live AWS credentials.
# Pass at synth/deploy time: cdk deploy -c hosted_zone_id=Z1234567890ABC
# In CI this is injected from the HOSTED_ZONE_ID Actions variable.
hosted_zone_id = app.node.try_get_context("hosted_zone_id") or ""

stack = ChannelStack(
    app, stack_id, env_name=env_name, hosted_zone_id=hosted_zone_id, env=env
)

cdk.Aspects.of(stack).add(AwsSolutionsChecks(verbose=True))

app.synth()
