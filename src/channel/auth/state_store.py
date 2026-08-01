# Copyright (c) 2026 John Carter. All rights reserved.
"""
DynamoDB-backed store for OAuth state parameters used by the management
UI login flow.

Replaces the in-process ``_pending_states`` dict that previously lived in
:mod:`starter.auth.mgmt_auth`. The dict pattern works under a single
warm Lambda but breaks under concurrent execution: the callback may hit
a different warm container than the one that issued the state, the
state is missing, the auth flow fails with "invalid state", and the user
gets a 400. The probability scales with concurrency.

Items use the ``MGMT_STATE#{state}`` / ``META`` key shape documented in
CLAUDE.md §"DynamoDB single table design". TTL is set on each item so
DynamoDB sweeps stale rows within ~48h, but the application code on the
read path also enforces ``created_at + ttl_seconds > now`` — DynamoDB's
TTL is best-effort and we do not trust it for correctness.

Module placement (auth-scoped, not a general-purpose storage layer) is
locked by issue #23 — do not relocate without revisiting that decision.
"""

from __future__ import annotations

import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from channel.logging_config import get_logger

logger = get_logger(__name__)

# Default TTL — 10 minutes is plenty for a Google round-trip and short
# enough that a leaked state has narrow exploit value.
_STATE_TTL_SECONDS_DEFAULT = 600


def _get_table() -> Any:
    """Return the DynamoDB Table resource for the configured table.

    Resolved at call time (not import time) so test fixtures that set
    ``CHANNEL_TABLE_NAME`` / ``DYNAMODB_ENDPOINT`` after the module is
    imported still take effect.

    Region resolution follows boto3 precedence: ``AWS_REGION`` →
    ``AWS_DEFAULT_REGION`` → ``~/.aws/config``. We only pass
    ``region_name`` when one of the env vars is explicitly set so
    local dev and CI inherit the configured default naturally.
    """
    table_name = os.environ.get("CHANNEL_TABLE_NAME", "channel-dev")
    endpoint_url = os.environ.get("DYNAMODB_ENDPOINT")  # set for DynamoDB Local
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    kwargs: dict[str, Any] = {"endpoint_url": endpoint_url}
    if region:
        kwargs["region_name"] = region
    dynamodb = boto3.resource("dynamodb", **kwargs)
    return dynamodb.Table(table_name)


def put_state(
    state: str,
    payload: dict[str, Any] | None = None,
    ttl_seconds: int = _STATE_TTL_SECONDS_DEFAULT,
) -> None:
    """Persist a pending OAuth state for later consumption by the callback.

    Writes a single ``MGMT_STATE#{state}`` / ``META`` row with a
    ``created_at`` timestamp and a ``ttl`` attribute (absolute Unix
    timestamp) so DynamoDB will sweep the row if it never gets consumed.

    The caller chooses ``ttl_seconds``; the default is 10 minutes which
    is generous for a Google OAuth round-trip and short enough that a
    leaked state has minimal exploit window.
    """
    now = int(time.time())
    item: dict[str, Any] = {
        "PK": f"MGMT_STATE#{state}",
        "SK": "META",
        "created_at": now,
        "ttl_seconds": ttl_seconds,
        "ttl": now + ttl_seconds,  # absolute Unix timestamp for DynamoDB TTL sweep
    }
    if payload:
        # Caller-supplied attributes (e.g. nonce, redirect_uri). Reserved
        # keys (PK / SK / created_at / ttl / ttl_seconds) take precedence.
        for k, v in payload.items():
            if k not in item:
                item[k] = v

    _get_table().put_item(Item=item)


def consume_state(state: str) -> dict[str, Any] | None:
    """Atomically read-and-delete a pending OAuth state.

    Returns the full deleted item on success, or ``None`` if the state
    is not present, has already been consumed, or has expired.

    The returned mapping is the DynamoDB old image from
    ``ReturnValues=ALL_OLD``. It includes the caller-supplied payload
    attributes (``nonce``, ``redirect_uri``, etc.) **as well as**
    reserved storage metadata: ``PK``, ``SK``, ``created_at``, ``ttl``,
    and ``ttl_seconds``. Callers that only want the payload they
    originally passed to :func:`put_state` should filter the reserved
    keys themselves — this layer doesn't strip them so log / metrics
    code can inspect the metadata when an auth flow fails.

    Implemented as a single conditional ``delete_item`` with
    ``ReturnValues=ALL_OLD``. Atomicity comes from DynamoDB's single-key
    write serialization, not from the condition. The condition exists
    to distinguish "state was never present" (callback for an
    unknown / replayed state) from "state was issued but expired"
    (legitimate user, slow callback). Both are auth-flow failure modes
    worth distinguishing in logs / metrics.

    The ConditionExpression checks presence only (``attribute_exists(PK)``).
    The expiry check happens in application code on the returned old
    image — we do NOT push the time comparison into the
    ConditionExpression, because DynamoDB TTL is best-effort and the
    application must own correctness on this read path.
    """
    pk = f"MGMT_STATE#{state}"
    try:
        resp = _get_table().delete_item(
            Key={"PK": pk, "SK": "META"},
            ConditionExpression="attribute_exists(PK)",
            ReturnValues="ALL_OLD",
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code == "ConditionalCheckFailedException":
            # Could be a replay, an already-consumed state, or a TTL sweep.
            # We log INFO (not WARN) — this is an expected auth-flow path,
            # not an exceptional one, but it is worth distinguishing from
            # the "expired" case below for metric / log analysis.
            logger.info("OAuth state not present in store (replay or already consumed)")
            return None
        if error_code == "ValidationException":
            # State is user-supplied (query param on /auth/callback).
            # Malformed/oversized values trigger DynamoDB
            # ValidationException — treat the same as "not present" so
            # the FastAPI layer surfaces a 400 (via the existing None-
            # return branch) rather than a 500. We log the *error code*
            # (not the raw state value — user-controlled, log-injection
            # risk) so operators can still distinguish this case from a
            # genuine missing-state in metrics / log analysis.
            logger.info(
                "OAuth state value rejected by DynamoDB validation; treating as not present",
                extra={"error_code": error_code},
            )
            return None
        raise

    old_image = resp.get("Attributes")
    if not old_image:
        # ConditionExpression succeeded but no Attributes returned — should
        # not happen with ALL_OLD on a successful conditional delete, but
        # treat it the same as "not present" for safety.
        logger.info("OAuth state delete returned no attributes (treating as not present)")
        return None

    created_at = int(old_image.get("created_at", 0))
    ttl_seconds = int(old_image.get("ttl_seconds", _STATE_TTL_SECONDS_DEFAULT))
    if created_at + ttl_seconds <= int(time.time()):
        # State was issued but the user took too long. The delete already
        # happened (good — single-use semantics held), but the caller
        # should not be allowed to complete the flow with an expired state.
        logger.info("OAuth state expired (issued too long ago)")
        return None

    return dict(old_image)
