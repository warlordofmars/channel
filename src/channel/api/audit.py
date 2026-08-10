# Copyright (c) 2026 John Carter. All rights reserved.
"""Audit-log query API — ``GET /api/audit/events`` (#601, epic #110).

The audit trail has had writers since #151 (``auth.logout``,
``auth.session_revoke``, ``auth.session_revoke_all``, and the
``memory.*`` verbs from #476/#477/#478) and a per-user read primitive
since #234. What it did not have was a way to ask the question an audit
actually poses — *every* audit event in this window, across every
actor. ``api/admin.py``'s user-detail view shows 25 events for one user
over 7 days, which is a UI affordance, not a compliance surface. This
module is that surface.

**Admin-only, and that gate is the whole authorization story.**
``require_admin`` (JWT ``role == "admin"``) is the only thing between a
signed-in user and every actor's audit history — which includes other
users' email addresses. This endpoint is therefore exactly as strong as
whatever decides the admin role; see #600, which splits
``is_admin_email`` off the sign-in allowlist onto a dedicated,
default-deny SSM parameter. Until that lands, "admin" and "can sign in"
are the same set on any stack whose ``ALLOWED_EMAILS`` is populated.
The correct response to an unreachable endpoint after #600 is to
populate the new parameter — never to weaken the gate here.

**Reading the audit log is itself an audit event, and that write is not
best-effort.** ``audit.read`` is recorded before the rows are returned,
and a failure to record it fails the request rather than serving the
data unrecorded. Every other ``put_audit_event`` call in this codebase
is deliberately swallowed, because by the time it runs the state change
it describes has already happened and failing the response would
misreport it. Here the ordering is the other way round: nothing has
been disclosed yet, so refusing to disclose is still available — and is
what "the trail records access to itself" has to mean. A trail whose
own reads go unlogged has a hole shaped exactly like the person most
motivated to use it.

**The window is capped, and the cap is reported.** A request wider than
:data:`_MAX_WINDOW_DAYS` is served for the most recent slice of it,
with ``window.capped = true`` and the originally requested bounds
echoed back. Silently truncating a compliance query is worse than
refusing it; saying what was actually covered lets the caller page the
remainder with a narrower ``from``.

**Pagination is an opaque cursor.** The token is base64url JSON
carrying the resume partition and sort key *plus* the window and
filters it was issued under. Rebinding a cursor to different filters
would silently skip or repeat rows, so a cursor presented alongside a
conflicting explicit parameter is a 400 rather than a best-effort
merge. ``to`` defaults to "now", which changes between requests —
embedding the window is what lets a client page without pinning it, and
the echoed ``window.to`` is what to send if it wants to.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import storage

# Both are module-private by naming convention and both are imported
# for the same reason ``auth/refresh.py`` imports the first: they are
# the canonical parsers for this table's own encodings, and the exact
# ones ``storage.query_audit_events`` applies to the values handed to it
# below. Re-spelling the ISO normalisation (trailing ``Z``, naive means
# UTC) or the ``AUDIT#{date}#{hour}`` key format here would be precisely
# the drift they exist to prevent.
from ..storage import _audit_shard_hour, _parse_iso_utc
from ._auth import require_admin

router = APIRouter(prefix="/audit", tags=["audit"])

# Widest window one request may cover. A day of audit events is 24
# hourly partitions and the storage walk visits every one of them, so
# an uncapped window is an unbounded fan-out. 31 days keeps "the whole
# of last month" a single query; wider ranges page with explicit
# bounds.
_MAX_WINDOW_DAYS = 31

# Window used when the caller names neither bound. Deliberately short —
# the common interactive question is "what just happened".
_DEFAULT_WINDOW = timedelta(hours=24)

# Cursor envelope version. Bumping it invalidates outstanding tokens,
# which is the intended behaviour if the payload's meaning changes.
_CURSOR_VERSION = 1

_AUDIT_READ_EVENT = "audit.read"


class AuditEvent(BaseModel):
    """One audit row, minus its DynamoDB plumbing.

    ``PK`` / ``SK`` / ``ttl`` are storage detail and never leave the
    server. ``actor_id`` *is* returned — unlike the per-user projection
    in ``api/admin.py``, which drops it as redundant — because
    across-actor attribution is the entire point of this surface.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str | None = None
    event_type: str | None = None
    actor_id: str | None = None
    created_at: str | None = None
    details: dict[str, Any] | None = None


class AuditWindow(BaseModel):
    """The window actually queried, alongside the one that was asked for.

    ``capped`` is the honest-truncation signal: when true, ``from`` is
    later than ``requested_from`` and the response covers only the tail
    of the requested range.

    There is deliberately no ``requested_to``: only the lower bound is
    ever moved. A ``to`` in the future is harmless — it simply matches
    nothing — so clamping it would adjust a bound without changing a
    result, and echoing a field that always equals ``to`` would be noise
    on a surface whose whole job is saying exactly what it covered.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # ``from`` is a Python keyword, so the field is ``from_`` and the
    # wire name comes from the alias. FastAPI serialises response models
    # ``by_alias``, so clients see ``from``.
    from_: str = Field(alias="from")
    to: str
    requested_from: str
    capped: bool
    max_window_days: int


class AuditEventsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[AuditEvent]
    window: AuditWindow
    next_cursor: str | None = None


def _str_or_none(value: Any) -> str | None:
    """Coerce a raw DDB attribute to ``str``, preserving a genuine absence.

    Audit rows are written by :func:`storage.put_audit_event` with
    string fields, but the read side has no schema enforcement — a row
    from a future writer, or one hand-written during an incident, must
    not 500 a whole page over one attribute.
    """

    return None if value is None else str(value)


def _json_numbers(value: Any) -> Any:
    """Turn DynamoDB ``Decimal``s inside ``details`` back into JSON numbers.

    Every DynamoDB Number reads back as ``decimal.Decimal``, and pydantic
    serialises a ``Decimal`` living in a ``dict[str, Any]`` as a *string*
    — so ``{"revoked_rows": 3}`` would leave here as
    ``{"revoked_rows": "3"}``. Consumers of a compliance export should
    not have to know the storage engine's number type, and a value that
    changes JSON type on the way through is exactly the kind of detail
    that breaks a downstream query months later.

    Integral values become ``int`` (exact, at any width); the rest
    become ``float``. Anything that is not a Decimal, dict or list is
    returned untouched — this normalises a representation, it does not
    sanitise content.
    """

    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _json_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_numbers(item) for item in value]
    return value


def _details_of(row: dict[str, Any]) -> dict[str, Any] | None:
    """Project a row's ``details`` map, or ``None`` when it has none.

    ``put_audit_event`` omits the attribute entirely when a caller passes
    no details, and a non-map value could only come from outside that
    writer — either way "no details" is the honest answer, and beats a
    validation error that would take the whole page down.
    """

    details = row.get("details")
    if not isinstance(details, dict):
        return None
    return {key: _json_numbers(value) for key, value in details.items()}


def _parse_bound(value: str, field: str) -> datetime:
    """Parse a caller-supplied ISO-8601 bound; 400 on anything else."""

    try:
        return _parse_iso_utc(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"invalid {field}: expected an ISO-8601 timestamp"
        ) from exc


def _encode_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode()


def _optional_str(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    return value is None or isinstance(value, str)


def _decode_cursor(token: str) -> dict[str, Any]:
    """Decode an opaque cursor; 400 on anything malformed or foreign.

    A corrupt, truncated or hand-rolled token is a client error, never a
    500 — and never a silently different query.

    That covers **shape as well as type, and presence as well as
    shape** — three separate ways a hand-rolled token used to reach an
    unhandled exception instead of a status code:

    * ``pk`` being a string is not enough; ``storage._audit_shard_hour``
      raises ``ValueError`` on anything that is not an
      ``AUDIT#{date}#{hour}`` key.
    * ``from`` / ``to`` being strings is not enough either; the caller
      parses them, and a well-typed non-date raises ``ValueError``.
    * ``sk`` / ``actor`` / ``event_type`` are *optional in value* but
      **required in presence** once past this point, because the caller
      indexes them. ``_optional_str`` cannot tell "absent" from "null",
      so absent is normalised to ``None`` here rather than left to
      surface as a ``KeyError`` two frames later.

    Nothing downstream would translate any of those into a status code,
    so validating all three here is what makes the docstring above true
    rather than aspirational.
    """

    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode()))
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("v") != _CURSOR_VERSION
        or not isinstance(payload.get("pk"), str)
        or not isinstance(payload.get("from"), str)
        or not isinstance(payload.get("to"), str)
        or not _optional_str(payload, "sk")
        or not _optional_str(payload, "actor")
        or not _optional_str(payload, "event_type")
    ):
        raise HTTPException(status_code=400, detail="invalid cursor")
    try:
        _audit_shard_hour(payload["pk"])
        _parse_iso_utc(payload["from"])
        _parse_iso_utc(payload["to"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    for optional in ("sk", "actor", "event_type"):
        payload.setdefault(optional, None)
    return payload


def _reject_cursor_conflict(field: str, supplied: str | None, embedded: str | None) -> None:
    """A cursor's window and filters win; a conflicting explicit param is a 400.

    Resuming a walk under different filters than the ones that produced
    the cursor would skip or repeat rows with no signal at all, so the
    mismatch is refused rather than silently resolved in either
    direction.
    """

    if supplied is not None and supplied != embedded:
        raise HTTPException(status_code=400, detail=f"cursor was issued for a different '{field}'")


@router.get("/events", response_model=AuditEventsResponse)
def read_audit_events(
    response: Response,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    claims: dict[str, Any] = Depends(require_admin),
) -> AuditEventsResponse:
    """Every audit event in a time window, newest first. Admin-only.

    ``from`` defaults to 24 hours before ``to``; ``to`` defaults to now.
    ``actor`` and ``event_type`` narrow the result server-side. Follow
    ``next_cursor`` to page — it carries the window and the filters, so
    nothing else needs repeating, and repeating one differently is a
    400.
    """

    # An audit surface must never be served from a heuristic cache — the
    # same reasoning as /api/me/sessions, where a stale answer is worse
    # than a slow one.
    response.headers["Cache-Control"] = "no-store"

    resume: dict[str, Any] | None = None
    if cursor is not None:
        resume = _decode_cursor(cursor)
        _reject_cursor_conflict(
            "from",
            None if from_ is None else _parse_bound(from_, "from").isoformat(),
            resume["from"],
        )
        _reject_cursor_conflict(
            "to",
            None if to is None else _parse_bound(to, "to").isoformat(),
            resume["to"],
        )
        _reject_cursor_conflict("actor", actor, resume["actor"])
        _reject_cursor_conflict("event_type", event_type, resume["event_type"])
        # The cursor stores the *requested* bounds, so the cap below is
        # recomputed identically on every page rather than compounding.
        requested_from_dt = _parse_iso_utc(resume["from"])
        to_dt = _parse_iso_utc(resume["to"])
        actor = resume["actor"]
        event_type = resume["event_type"]
    else:
        to_dt = datetime.now(timezone.utc) if to is None else _parse_bound(to, "to")
        requested_from_dt = (
            to_dt - _DEFAULT_WINDOW if from_ is None else _parse_bound(from_, "from")
        )

    if requested_from_dt > to_dt:
        raise HTTPException(status_code=400, detail="'from' must not be after 'to'")

    earliest = to_dt - timedelta(days=_MAX_WINDOW_DAYS)
    capped = requested_from_dt < earliest
    from_dt = earliest if capped else requested_from_dt

    try:
        events, next_key = storage.query_audit_events(
            from_iso=from_dt.isoformat(),
            to_iso=to_dt.isoformat(),
            actor_id=actor,
            event_type=event_type,
            limit=limit,
            cursor=None if resume is None else {"PK": resume["pk"], "SK": resume["sk"]},
        )
    except ClientError as exc:
        # The last way a forged cursor could still 500. A well-typed
        # ``sk`` that is not a valid resume position for *this* window —
        # anything outside the sort-key range the window derives — makes
        # DynamoDB answer "The provided starting key does not match the
        # range key predicate", and that is a client error by the same
        # reasoning as every other check in ``_decode_cursor``. It can't
        # be settled there: whether an ``sk`` is in range is a function
        # of the window, which is resolved here.
        #
        # Narrow on both axes deliberately. Only when a cursor was
        # supplied — nothing else on this path can produce an invalid
        # start key — and only for ``ValidationException``, so a genuine
        # storage fault still surfaces as the 500 it is rather than
        # being reported to the caller as their fault.
        if resume is None or exc.response.get("Error", {}).get("Code") != "ValidationException":
            raise
        raise HTTPException(status_code=400, detail="invalid cursor") from exc

    # Recorded before the rows are returned, and allowed to fail the
    # request — see the module docstring. ``details`` carries the shape
    # of the question and the size of the answer, never the rows
    # themselves: an audit row must not become a second copy of the
    # trail it records (the same rule ``api/memory.py`` states for its
    # export and forget verbs).
    storage.put_audit_event(
        event_type=_AUDIT_READ_EVENT,
        actor_id=str(claims.get("sub") or ""),
        details={
            "role": claims.get("role", "user"),
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "actor_filter": actor,
            "event_type_filter": event_type,
            "returned": len(events),
            "paged": cursor is not None,
            "window_capped": capped,
        },
    )

    return AuditEventsResponse(
        events=[
            AuditEvent(
                event_id=_str_or_none(row.get("event_id")),
                event_type=_str_or_none(row.get("event_type")),
                actor_id=_str_or_none(row.get("actor_id")),
                created_at=_str_or_none(row.get("created_at")),
                details=_details_of(row),
            )
            for row in events
        ],
        window=AuditWindow(
            from_=from_dt.isoformat(),
            to=to_dt.isoformat(),
            requested_from=requested_from_dt.isoformat(),
            capped=capped,
            max_window_days=_MAX_WINDOW_DAYS,
        ),
        next_cursor=(
            None
            if next_key is None
            else _encode_cursor(
                {
                    "v": _CURSOR_VERSION,
                    "pk": next_key["PK"],
                    "sk": next_key["SK"],
                    "from": requested_from_dt.isoformat(),
                    "to": to_dt.isoformat(),
                    "actor": actor,
                    "event_type": event_type,
                }
            )
        ),
    )
