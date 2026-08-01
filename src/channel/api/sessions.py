# Copyright (c) 2026 John Carter. All rights reserved.
"""Session management API — GET / DELETE ``/api/me/sessions`` (#293, epic #241).

A *session* here is a signed-in **device**, not a single refresh row.
Under the hard rotation #290 implements, a device burns through a chain
of refresh rows — every refresh revokes the presented row and mints a
successor — so the durable identity of "this laptop is signed in" is the
``device_id`` shared by that chain, and the live row is just its current
tip. These routes therefore collapse the caller's live refresh rows into
one entry per device.

Three routes, all requiring a valid mgmt JWT:

* ``GET /api/me/sessions`` — the caller's live sessions.
* ``DELETE /api/me/sessions/{device_id}`` — sign out one device.
* ``DELETE /api/me/sessions`` — sign out every device, including the
  caller's own.

**Ownership is scoped by the JWT ``sub`` claim, and a mismatch is a 404.**
``_load_owned_session`` mirrors ``_load_owned_chat`` in ``api/chats.py``
(CLAUDE.md §Product decisions, "chat scope is enforced via the
chat-index ownership check"): every lookup runs inside the caller's own
``REFRESH_USER#{sub}`` index partition, so another user's ``device_id``
is simply absent and falls out as "not found". Returning 403 would
confirm the device exists and turn this endpoint into an oracle for
other users' device ids.

**Revoking a session does not immediately kill outstanding access
tokens.** These routes revoke *refresh* rows, which stops the device
from minting further access tokens; a mgmt JWT already issued to that
device stays valid until its own ``exp`` unless its ``jti`` is also
denylisted (``/auth/logout``, #240). With the 1-hour access-token TTL
that epic #241 restores (#291) the residual window is bounded by that
TTL. Anything needing instant cutoff must go through the denylist.

**The reads are eventually consistent** — they walk
``RefreshByUserIndex``, and DynamoDB refuses ``ConsistentRead`` on a
GSI. Two user-visible consequences, both deliberate rather than papered
over:

1. A session created moments ago may be missing from ``GET`` and, for
   the same reason, revoking it may 404 until the index catches up.
2. ``DELETE`` returns 204 with no revoked-row count. The count would
   read as a post-condition ("3 sessions ended") that the storage layer
   explicitly does not promise: ``_revoke_refresh_family`` is
   best-effort with respect to concurrent mints, so a row minted inside
   the propagation window can survive the sweep. 204 states what is
   true — the revoke was applied — without implying a stronger
   guarantee than the index can support.

Closing that window needs a strongly-consistent per-device marker read
on the consume path (the direction CLAUDE.md sketches under the
refresh-token item family); that is a change to the mint/consume path,
not to this read-and-revoke surface, and is deliberately not attempted
here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict

from .. import storage
from ._auth import require_mgmt_user

router = APIRouter(prefix="/me/sessions", tags=["sessions"])


class Session(BaseModel):
    """One signed-in device, projected from its live refresh row.

    Deliberately a hand-written projection rather than a dump of
    ``RefreshToken``: that model carries ``token_hash``, and while a
    SHA-256 digest is not a usable credential, handing every client a
    stable per-token identifier is a needless correlation handle. The
    fields below are exactly what a "your active sessions" view needs.

    ``device_id`` is the only device identity #290 persists — there is
    no human-readable label column, so a friendlier name ("MacBook Pro",
    "Chrome on Windows") needs a new attribute written at mint time and
    belongs with the login path, not here.
    """

    model_config = ConfigDict(extra="forbid")

    device_id: str
    issued_at: str
    last_used_at: str
    absolute_expires_at: str
    idle_expires_at: str


class SessionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessions: list[Session]


def _sessions_for_user(user_id: str) -> list[Session]:
    """Collapse the caller's live refresh rows into one entry per device.

    ``storage.list_live_refresh_tokens`` returns newest-first, so the
    first row seen for a device wins. Hard rotation should leave exactly
    one live row per device, but a refresh racing this read can briefly
    leave two (the successor is live before the caller sees it), and the
    newer row is the one the device actually holds.
    """

    by_device: dict[str, Session] = {}
    for row in storage.list_live_refresh_tokens(user_id):
        if row.device_id in by_device:
            continue
        by_device[row.device_id] = Session(
            device_id=row.device_id,
            issued_at=row.issued_at,
            last_used_at=row.last_used_at,
            absolute_expires_at=row.absolute_expires_at,
            idle_expires_at=row.idle_expires_at,
        )
    return list(by_device.values())


def _load_owned_session(device_id: str, user_id: str) -> Session:
    """Look up one live session by device id and assert ownership. 404 on mismatch.

    The direct analogue of ``_load_owned_chat``: the lookup is confined
    to the caller's own index partition, so "belongs to another user" and
    "does not exist" are indistinguishable from the outside — which is
    the point. See the module docstring for why this is 404 and not 403.

    A device whose rows are all revoked or expired is also "not found":
    it has no session left to end, so a second delete of the same device
    is a 404 rather than a silent success. That is the same shape as
    deleting an already-deleted chat.
    """

    for session in _sessions_for_user(user_id):
        if session.device_id == device_id:
            return session
    raise HTTPException(status_code=404, detail="Session not found")


@router.get("", response_model=SessionListResponse)
def list_my_sessions(
    response: Response,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> SessionListResponse:
    """List the caller's live sessions, most recently issued first."""

    # Same reasoning as /api/me/prefs, with more at stake: without an
    # explicit no-store, Chromium (browser + Electron renderer)
    # heuristic-caches the response, so a device the user just signed
    # out of keeps appearing in the list.
    response.headers["Cache-Control"] = "no-store"
    return SessionListResponse(sessions=_sessions_for_user(claims["sub"]))


@router.delete("/{device_id}", status_code=204)
def revoke_my_session(
    device_id: str = Path(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Revoke one device's refresh-token family. 204, or 404 if not the caller's."""

    user_id = claims["sub"]
    _load_owned_session(device_id, user_id)
    storage.revoke_device_refresh_tokens(user_id, device_id)
    return Response(status_code=204)


@router.delete("", status_code=204)
def revoke_all_my_sessions(
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Revoke every refresh-token family for the caller ("sign out everywhere").

    Includes the calling device — there is no ``device_id`` claim on the
    mgmt JWT to exclude it by, and "sign out everywhere" that quietly
    spared the device issuing the request would be the more surprising
    behaviour for a user who believes their account is compromised.

    Idempotent: a caller with no live sessions still gets 204.
    """

    storage.revoke_all_user_refresh_tokens(claims["sub"])
    return Response(status_code=204)
