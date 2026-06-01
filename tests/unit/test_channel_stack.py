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
