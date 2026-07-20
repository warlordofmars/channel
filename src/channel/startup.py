# Copyright (c) 2026 John Carter. All rights reserved.
"""
Startup-time validation hooks for SSM-backed configuration.

Two hooks are exposed:

- :func:`validate_secrets_or_die` — hard-fail validator for the four
  security-critical SSM parameters. Raises :class:`StartupConfigError` on
  the first unrotated placeholder so AWS Lambda init fails and
  CloudFormation reports the deploy as failed.

- :func:`warn_unrotated_observability_params` — soft-warn check for the
  operational :code:`AlarmEmail` parameter. Logs a single ``WARNING`` per
  cold start when the value is still the placeholder; never raises.

Both hooks short-circuit when ``AWS_LAMBDA_FUNCTION_NAME`` is unset, so
``inv dev``, unit tests, and integration tests against DynamoDB Local
are unaffected.

Wired at module import in :mod:`channel.api.main` (not via
``@app.on_event("startup")``) so the failure surfaces during Lambda INIT
rather than after the container has been marked ready.
"""

from __future__ import annotations

import os

from channel.logging_config import get_logger

logger = get_logger(__name__)

# The placeholder string used by the CDK stack when the SSM parameter is
# first provisioned. Any value still equal to this string at startup
# means the operator has not yet rotated the secret.
PLACEHOLDER_VALUE = "CHANGE_ME_ON_FIRST_DEPLOY"

# Env vars that hold the SSM parameter name for the four security-critical
# parameters. Names match the ones wired by :mod:`infra.stacks.starter_stack`
# under ``common_env`` — do not hardcode parameter paths here.
HARD_FAIL_PARAM_ENV_VARS: tuple[str, ...] = (
    "CHANNEL_JWT_SECRET_PARAM",
    "GOOGLE_CLIENT_ID_PARAM",
    "GOOGLE_CLIENT_SECRET_PARAM",
    "CHANNEL_ORIGIN_VERIFY_PARAM",
)

# Env var that holds the SSM parameter name for the operational
# AlarmEmail parameter (soft-warn only).
SOFT_WARN_PARAM_ENV_VAR = "CHANNEL_ALARM_EMAIL_PARAM"


class StartupConfigError(RuntimeError):
    """Raised when a security-critical SSM parameter is unrotated.

    Surfaces during AWS Lambda init when wired at module import; aborts
    the cold start and is reported by CloudFormation as a deploy failure.
    """


def _on_lambda() -> bool:
    """Return True only when running inside AWS Lambda.

    Both startup hooks are no-ops outside Lambda so local development,
    unit tests, and DynamoDB-Local integration tests are unaffected.
    """
    return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def _get_ssm_value(parameter_name: str) -> str:
    """Read an SSM parameter value with decryption.

    Imported lazily so test environments without :mod:`boto3` can still
    import this module. Any exception (parameter missing, permission
    denied, transient SSM failure) propagates — the silent-fallback
    pattern that masked SEC-3/SEC-5 is exactly what this module exists
    to prevent.
    """
    import boto3

    ssm = boto3.client("ssm")
    resp = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
    return resp["Parameter"]["Value"]


def validate_secrets_or_die() -> None:
    """Hard-fail validator for security-critical SSM secrets.

    Iterates over :data:`HARD_FAIL_PARAM_ENV_VARS`, resolves each to its
    SSM parameter name, fetches the value, and raises
    :class:`StartupConfigError` on the first match against
    :data:`PLACEHOLDER_VALUE`. SSM exceptions propagate — they must
    surface, not be swallowed.

    No-op when ``AWS_LAMBDA_FUNCTION_NAME`` is unset.
    """
    if not _on_lambda():
        return

    for env_var in HARD_FAIL_PARAM_ENV_VARS:
        param_name = os.environ.get(env_var)
        if not param_name:
            # The stack always wires these; an unset value at runtime is
            # itself a deploy bug. Surface it loudly rather than skipping.
            raise StartupConfigError(
                f"Required env var {env_var} is unset; cannot validate SSM parameter"
            )
        value = _get_ssm_value(param_name)
        if value == PLACEHOLDER_VALUE:
            raise StartupConfigError(
                f"SSM parameter {param_name} still has placeholder value "
                f"{PLACEHOLDER_VALUE!r}; rotate it before the service can start"
            )


def warn_unrotated_observability_params() -> None:
    """Soft-warn for operational SSM params still at placeholder values.

    Currently covers :data:`SOFT_WARN_PARAM_ENV_VAR` (AlarmEmail). Logs
    a single ``WARNING`` per cold start when the value is unrotated;
    does not raise. SSM exceptions propagate.

    No-op when ``AWS_LAMBDA_FUNCTION_NAME`` is unset.
    """
    if not _on_lambda():
        return

    param_name = os.environ.get(SOFT_WARN_PARAM_ENV_VAR)
    if not param_name:
        # Operational env var unset → nothing to check. Distinct from the
        # hard-fail path: missing operational wiring is annoying, not
        # exploitable, so we warn and return.
        logger.warning(
            "Env var %s is unset; alarm-email placeholder check skipped",
            SOFT_WARN_PARAM_ENV_VAR,
        )
        return

    value = _get_ssm_value(param_name)
    if value == PLACEHOLDER_VALUE:
        logger.warning(
            "SSM parameter %s still has placeholder value %r; CloudWatch alarms "
            "will route to nowhere until this is rotated",
            param_name,
            PLACEHOLDER_VALUE,
        )
