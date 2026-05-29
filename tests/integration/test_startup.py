# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration test: app refuses to import under simulated Lambda env.

Verifies the wire-at-import contract — :mod:`channel.api.main` must
raise :class:`channel.startup.StartupConfigError` during module
construction when any of the four security-critical SSM parameters is
still set to ``CHANGE_ME_ON_FIRST_DEPLOY``. Wiring at module import
(not via ``@app.on_event("startup")``) is what causes Lambda INIT to
fail, which CloudFormation reports as a deploy failure.

Separately verifies the soft-warn path emits exactly one ``WARNING``
log line for ``AlarmEmail`` per cold start and does not raise.
"""

from __future__ import annotations

import importlib
import logging
import sys

import pytest

from channel import startup
from channel.startup import (
    HARD_FAIL_PARAM_ENV_VARS,
    PLACEHOLDER_VALUE,
    SOFT_WARN_PARAM_ENV_VAR,
)


def _force_reimport_main() -> None:
    """Drop cached channel.api.main + channel.startup so import re-runs hooks.

    Each test simulates a Lambda cold start, where the module-level
    ``validate_secrets_or_die()`` call fires during import. Without
    eviction the second test would pick up the cached, already-validated
    module and the wire-at-import contract would not actually be tested.
    """
    for mod in (
        "channel.api.main",
        "channel.api._auth",
        "channel.startup",
    ):
        sys.modules.pop(mod, None)


@pytest.fixture
def lambda_env(monkeypatch):
    """Simulate the Lambda runtime: AWS_LAMBDA_FUNCTION_NAME + wired SSM env vars."""
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "channel-api")
    for ev in HARD_FAIL_PARAM_ENV_VARS:
        monkeypatch.setenv(ev, f"/test/{ev.lower()}")
    monkeypatch.setenv(SOFT_WARN_PARAM_ENV_VAR, "/test/alarm-email")
    # JWT secret env var has highest priority in tokens.py — set it so the
    # downstream import path doesn't try to reach SSM during the test.
    monkeypatch.setenv("STARTER_JWT_SECRET", "integration-test-secret")
    yield
    _force_reimport_main()


@pytest.mark.parametrize("placeholder_env_var", HARD_FAIL_PARAM_ENV_VARS)
def test_app_import_fails_when_any_hard_fail_param_is_placeholder(
    monkeypatch, lambda_env, placeholder_env_var
):
    """Module import must raise StartupConfigError naming the unrotated param."""
    placeholder_path = f"/test/{placeholder_env_var.lower()}"

    def _fake_ssm(name: str) -> str:
        return PLACEHOLDER_VALUE if name == placeholder_path else "rotated-value"

    # Re-importing channel.startup creates a *fresh* class object for
    # StartupConfigError — the class imported at the top of this file is
    # from the previously-loaded module, so pytest.raises() must match
    # against the freshly re-imported symbol.
    _force_reimport_main()
    fresh_startup = importlib.import_module("channel.startup")
    monkeypatch.setattr(fresh_startup, "_get_ssm_value", _fake_ssm)
    fresh_error = fresh_startup.StartupConfigError

    # Now the api.main import will run validate_secrets_or_die() which calls
    # the patched _get_ssm_value.
    sys.modules.pop("channel.api.main", None)
    with pytest.raises(fresh_error) as excinfo:
        importlib.import_module("channel.api.main")

    assert placeholder_path in str(excinfo.value)


def test_app_import_emits_single_warning_for_alarm_email_placeholder(monkeypatch, lambda_env):
    """Soft-warn must log exactly one WARNING for AlarmEmail, not raise.

    The project's structured logger sets ``propagate=False`` on the
    ``starter`` tree, so :data:`pytest`'s caplog cannot see records via
    the root. Attach a dedicated handler to capture them.
    """
    alarm_path = "/test/alarm-email"

    def _fake_ssm(name: str) -> str:
        return PLACEHOLDER_VALUE if name == alarm_path else "rotated-value"

    _force_reimport_main()
    fresh_startup = importlib.import_module("channel.startup")
    monkeypatch.setattr(fresh_startup, "_get_ssm_value", _fake_ssm)

    captured: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _ListHandler(level=logging.DEBUG)
    startup_logger = logging.getLogger("channel.startup")
    startup_logger.addHandler(handler)
    try:
        sys.modules.pop("channel.api.main", None)
        importlib.import_module("channel.api.main")  # must succeed
    finally:
        startup_logger.removeHandler(handler)

    placeholder_warnings = [
        r for r in captured if r.levelno == logging.WARNING and alarm_path in r.getMessage()
    ]
    assert len(placeholder_warnings) == 1


def test_app_import_succeeds_when_all_params_rotated(monkeypatch, lambda_env):
    """Sanity: with every param rotated the app imports cleanly under Lambda env."""
    _force_reimport_main()
    fresh_startup = importlib.import_module("channel.startup")
    monkeypatch.setattr(fresh_startup, "_get_ssm_value", lambda _name: "rotated-value")

    sys.modules.pop("channel.api.main", None)
    main = importlib.import_module("channel.api.main")
    assert hasattr(main, "app")


def teardown_module() -> None:
    """Restore the real channel.api.main for any tests that import it later."""
    _force_reimport_main()
    # Re-import a clean copy without the lambda env so subsequent suites get
    # the normal local-dev module.
    importlib.import_module("channel.startup")
    importlib.import_module("channel.api.main")
    # Touch the public name so static analysis doesn't flag the import as unused.
    assert startup.PLACEHOLDER_VALUE == PLACEHOLDER_VALUE
