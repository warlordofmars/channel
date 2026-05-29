# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the fail-closed startup validation hooks."""

from __future__ import annotations

import logging

import pytest

from channel import startup
from channel.startup import (
    HARD_FAIL_PARAM_ENV_VARS,
    PLACEHOLDER_VALUE,
    SOFT_WARN_PARAM_ENV_VAR,
    StartupConfigError,
    validate_secrets_or_die,
    warn_unrotated_observability_params,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _set_lambda_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend we are running inside an AWS Lambda."""
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "channel-api")


def _wire_hard_fail_param_names(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set every hard-fail env var to a unique synthetic SSM parameter path."""
    mapping = {ev: f"/test/{ev.lower()}" for ev in HARD_FAIL_PARAM_ENV_VARS}
    for env_var, value in mapping.items():
        monkeypatch.setenv(env_var, value)
    return mapping


class _ListHandler(logging.Handler):
    """In-memory log handler used to capture records on the non-propagating
    ``channel`` logger tree (the project's JSON logger sets
    ``propagate=False``, so :data:`pytest`'s caplog fixture cannot see
    these records via the root).
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture_startup_logs():
    """Yield a list of records emitted on ``channel.startup``."""
    handler = _ListHandler()
    logger = logging.getLogger("channel.startup")
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


# ---------------------------------------------------------------------------
# Lambda short-circuit (both hooks)
# ---------------------------------------------------------------------------


def test_validate_secrets_or_die_no_op_outside_lambda(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)

    def _boom(_: str) -> str:  # pragma: no cover — must not be called
        raise AssertionError("SSM should not be touched outside Lambda")

    monkeypatch.setattr(startup, "_get_ssm_value", _boom)
    validate_secrets_or_die()  # must return cleanly


def test_warn_unrotated_observability_params_no_op_outside_lambda(
    monkeypatch, capture_startup_logs
):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)

    def _boom(_: str) -> str:  # pragma: no cover — must not be called
        raise AssertionError("SSM should not be touched outside Lambda")

    monkeypatch.setattr(startup, "_get_ssm_value", _boom)
    warn_unrotated_observability_params()
    assert capture_startup_logs == []


# ---------------------------------------------------------------------------
# validate_secrets_or_die — hard-fail behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("placeholder_env_var", HARD_FAIL_PARAM_ENV_VARS)
def test_validate_secrets_or_die_rejects_each_placeholder(monkeypatch, placeholder_env_var):
    """Each hard-fail parameter must be rejected on its own."""
    _set_lambda_env(monkeypatch)
    param_paths = _wire_hard_fail_param_names(monkeypatch)
    placeholder_path = param_paths[placeholder_env_var]

    def _fake_ssm(name: str) -> str:
        return PLACEHOLDER_VALUE if name == placeholder_path else "rotated-secret-value"

    monkeypatch.setattr(startup, "_get_ssm_value", _fake_ssm)

    with pytest.raises(StartupConfigError) as excinfo:
        validate_secrets_or_die()
    assert placeholder_path in str(excinfo.value)
    assert PLACEHOLDER_VALUE in str(excinfo.value)


def test_validate_secrets_or_die_passes_when_all_rotated(monkeypatch):
    _set_lambda_env(monkeypatch)
    _wire_hard_fail_param_names(monkeypatch)
    monkeypatch.setattr(startup, "_get_ssm_value", lambda _name: "rotated-secret-value")

    validate_secrets_or_die()  # must not raise


def test_validate_secrets_or_die_raises_when_param_env_var_missing(monkeypatch):
    """An unset env var means a deploy bug — surface it, don't skip."""
    _set_lambda_env(monkeypatch)
    _wire_hard_fail_param_names(monkeypatch)
    monkeypatch.delenv(HARD_FAIL_PARAM_ENV_VARS[0], raising=False)
    monkeypatch.setattr(startup, "_get_ssm_value", lambda _name: "rotated-secret-value")

    with pytest.raises(StartupConfigError) as excinfo:
        validate_secrets_or_die()
    assert HARD_FAIL_PARAM_ENV_VARS[0] in str(excinfo.value)


def test_validate_secrets_or_die_propagates_ssm_errors(monkeypatch):
    """SSM permission/missing-param errors must surface, not be swallowed."""
    _set_lambda_env(monkeypatch)
    _wire_hard_fail_param_names(monkeypatch)

    class _AccessDenied(Exception):
        pass

    def _raise(_name: str) -> str:
        raise _AccessDenied("ssm:GetParameter denied")

    monkeypatch.setattr(startup, "_get_ssm_value", _raise)
    with pytest.raises(_AccessDenied):
        validate_secrets_or_die()


# ---------------------------------------------------------------------------
# warn_unrotated_observability_params — soft-warn behaviour
# ---------------------------------------------------------------------------


def test_warn_logs_when_alarm_email_is_placeholder(monkeypatch, capture_startup_logs):
    _set_lambda_env(monkeypatch)
    monkeypatch.setenv(SOFT_WARN_PARAM_ENV_VAR, "/test/alarm-email")
    monkeypatch.setattr(startup, "_get_ssm_value", lambda _name: PLACEHOLDER_VALUE)

    warn_unrotated_observability_params()

    placeholder_records = [r for r in capture_startup_logs if "/test/alarm-email" in r.getMessage()]
    assert len(placeholder_records) == 1
    assert placeholder_records[0].levelno == logging.WARNING
    assert "alarms" in placeholder_records[0].getMessage().lower()


def test_warn_silent_when_alarm_email_is_rotated(monkeypatch, capture_startup_logs):
    _set_lambda_env(monkeypatch)
    monkeypatch.setenv(SOFT_WARN_PARAM_ENV_VAR, "/test/alarm-email")
    monkeypatch.setattr(startup, "_get_ssm_value", lambda _name: "ops@example.com")

    warn_unrotated_observability_params()

    assert capture_startup_logs == []


def test_warn_logs_when_param_env_var_unset(monkeypatch, capture_startup_logs):
    """Operational env var unset → log a single warning and return; do not raise."""
    _set_lambda_env(monkeypatch)
    monkeypatch.delenv(SOFT_WARN_PARAM_ENV_VAR, raising=False)

    def _boom(_: str) -> str:  # pragma: no cover — must not be called
        raise AssertionError("SSM should not be queried when env var is unset")

    monkeypatch.setattr(startup, "_get_ssm_value", _boom)
    warn_unrotated_observability_params()

    skipped = [r for r in capture_startup_logs if SOFT_WARN_PARAM_ENV_VAR in r.getMessage()]
    assert len(skipped) == 1
    assert skipped[0].levelno == logging.WARNING


def test_warn_propagates_ssm_errors(monkeypatch):
    """Soft-warn must still propagate SSM exceptions — silent absorb is the bug."""
    _set_lambda_env(monkeypatch)
    monkeypatch.setenv(SOFT_WARN_PARAM_ENV_VAR, "/test/alarm-email")

    class _Boom(Exception):
        pass

    def _raise(_name: str) -> str:
        raise _Boom("ssm:GetParameter denied")

    monkeypatch.setattr(startup, "_get_ssm_value", _raise)
    with pytest.raises(_Boom):
        warn_unrotated_observability_params()


# ---------------------------------------------------------------------------
# _get_ssm_value — boto3 boundary
# ---------------------------------------------------------------------------


def test_get_ssm_value_returns_decrypted_parameter(monkeypatch):
    """_get_ssm_value should call boto3.client('ssm').get_parameter with decryption."""
    captured: dict[str, object] = {}

    class _FakeSSM:
        def get_parameter(self, *, Name: str, WithDecryption: bool):
            captured["Name"] = Name
            captured["WithDecryption"] = WithDecryption
            return {"Parameter": {"Value": "rotated-value"}}

    def _fake_client(service: str):
        assert service == "ssm"
        return _FakeSSM()

    import boto3

    monkeypatch.setattr(boto3, "client", _fake_client)

    assert startup._get_ssm_value("/test/param") == "rotated-value"
    assert captured == {"Name": "/test/param", "WithDecryption": True}


# ---------------------------------------------------------------------------
# StartupConfigError sanity
# ---------------------------------------------------------------------------


def test_startup_config_error_is_runtime_error_subclass():
    assert issubclass(StartupConfigError, RuntimeError)
