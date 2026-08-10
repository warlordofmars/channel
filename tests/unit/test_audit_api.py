# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the /api/audit router (#601, epic #110).

Auth-gating tests drive the REAL ``require_admin`` dependency with real
mgmt JWTs (repo rule: don't mock auth; mock the AWS boundary), so the
403 / 401 boundary proven here is the one production enforces. Storage
is patched at the ``channel.api.audit.storage`` module seam, following
the test_admin_api.py pattern.

The properties this suite exists to pin, beyond ordinary happy-path
shape:

* the endpoint is admin-only, and a plain signed-in user is refused;
* a served read writes an ``audit.read`` row, and a *failure* to write
  it fails the request instead of disclosing the rows anyway;
* a window wider than the cap is reported as capped rather than
  silently truncated;
* a cursor cannot be rebound to a different window or filter set.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("CHANNEL_JWT_SECRET", "test-secret-for-unit-tests")

from channel.api.audit import _CURSOR_VERSION, _MAX_WINDOW_DAYS  # noqa: E402
from channel.api.main import app  # noqa: E402
from channel.auth.tokens import issue_mgmt_jwt  # noqa: E402

client = TestClient(app)


def _admin_headers() -> dict[str, str]:
    token = issue_mgmt_jwt(
        {"user_id": "admin@test.com", "email": "admin@test.com", "role": "admin"}
    )
    return {"Authorization": f"Bearer {token}"}


def _user_headers() -> dict[str, str]:
    token = issue_mgmt_jwt({"user_id": "user@test.com", "email": "user@test.com", "role": "user"})
    return {"Authorization": f"Bearer {token}"}


def _row(event_id: str, **attrs: Any) -> dict[str, Any]:
    """A raw audit item as ``storage.query_audit_events`` returns it."""

    return {
        "PK": "AUDIT#2026-08-09#12",
        "SK": f"1754740800#{event_id}",
        "event_id": event_id,
        "event_type": "auth.logout",
        "actor_id": "someone@test.com",
        "created_at": "2026-08-09T12:00:00.000000+00:00",
        "ttl": Decimal("1786276800"),
        **attrs,
    }


class _Storage:
    """Captures the storage seam the router drives.

    ``query_audit_events`` records its kwargs so the tests can assert on
    the window and filters the route derived, and returns whatever the
    test staged. ``put_audit_event`` is captured (and optionally made to
    raise) because the self-audit write's *failure* behaviour is one of
    the contracts under test.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.rows: list[dict[str, Any]] = []
        self.next_cursor: dict[str, Any] | None = None
        self.audit_events: list[dict[str, Any]] = []
        self.audit_raises = False

    def query_audit_events(self, **kwargs: Any) -> tuple[list[dict[str, Any]], Any]:
        self.calls.append(kwargs)
        return self.rows, self.next_cursor

    def put_audit_event(
        self, *, event_type: str, actor_id: str, details: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.audit_raises:
            raise RuntimeError("audit table unavailable")
        event = {
            "event_type": event_type,
            "actor_id": actor_id,
            "details": details or {},
        }
        self.audit_events.append(event)
        return event


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _Storage:
    fake = _Storage()
    for name in ("query_audit_events", "put_audit_event"):
        monkeypatch.setattr(f"channel.api.audit.storage.{name}", getattr(fake, name))
    return fake


def _cursor(*, drop: tuple[str, ...] = (), **overrides: Any) -> str:
    """A valid cursor envelope, optionally corrupted for a negative test.

    ``drop`` removes a key entirely, which is a distinct failure shape
    from setting it to a wrong type: an absent key satisfies every
    ``payload.get(...)`` check and only blows up later, at the point
    something indexes it.
    """

    payload: dict[str, Any] = {
        "v": _CURSOR_VERSION,
        "pk": "AUDIT#2026-08-09#12",
        "sk": "1754740800#evt-1",
        "from": "2026-08-01T00:00:00+00:00",
        "to": "2026-08-09T00:00:00+00:00",
        "actor": None,
        "event_type": None,
    }
    payload.update(overrides)
    for key in drop:
        payload.pop(key, None)
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode()


# ----------------------------------------------------------------
# Auth boundary — the endpoint, not the SPA, enforces admin
# ----------------------------------------------------------------


def test_audit_events_rejects_missing_auth() -> None:
    # HTTPBearer answers 403 when no Authorization header is supplied;
    # either status is "rejected for missing auth".
    assert client.get("/api/audit/events").status_code in (401, 403)


def test_audit_events_rejects_non_admin(store: _Storage) -> None:
    r = client.get("/api/audit/events", headers=_user_headers())

    assert r.status_code == 403
    # A refused read must not reach storage at all — neither the query
    # nor the self-audit row.
    assert store.calls == []
    assert store.audit_events == []


# ----------------------------------------------------------------
# Happy path + response shape
# ----------------------------------------------------------------


def test_returns_events_without_ddb_plumbing(store: _Storage) -> None:
    store.rows = [_row("evt-1")]

    r = client.get("/api/audit/events", headers=_admin_headers())

    assert r.status_code == 200
    body = r.json()
    assert len(body["events"]) == 1
    event = body["events"][0]
    assert event == {
        "event_id": "evt-1",
        "event_type": "auth.logout",
        "actor_id": "someone@test.com",
        "created_at": "2026-08-09T12:00:00.000000+00:00",
        "details": None,
    }
    # PK / SK / ttl are storage detail and never leave the server.
    assert not {"PK", "SK", "ttl"} & set(event)


def test_actor_id_is_returned_unlike_the_admin_detail_projection(
    store: _Storage,
) -> None:
    """Across-actor attribution is the point of this surface."""

    store.rows = [_row("evt-1", actor_id="victim@test.com")]

    r = client.get("/api/audit/events", headers=_admin_headers())

    assert r.json()["events"][0]["actor_id"] == "victim@test.com"


def test_details_are_passed_through(store: _Storage) -> None:
    store.rows = [_row("evt-1", details={"role": "user", "device_id": "laptop"})]

    r = client.get("/api/audit/events", headers=_admin_headers())

    assert r.json()["events"][0]["details"] == {"role": "user", "device_id": "laptop"}


def test_details_numbers_stay_numbers_on_the_wire(store: _Storage) -> None:
    """A DDB Decimal must not reach a consumer as a JSON *string*.

    Every Number reads back as ``Decimal``, which pydantic serialises as
    a string inside a ``dict[str, Any]`` — so without normalisation
    ``revoked_rows: 3`` would arrive as ``"3"``.
    """

    store.rows = [
        _row(
            "evt-1",
            details={
                "revoked_rows": Decimal("3"),
                "ratio": Decimal("0.5"),
                "big": Decimal("12345678901234567890"),
                "nested": {"count": Decimal("1")},
                "listed": [Decimal("2"), "text"],
                "flag": True,
                "text": "unchanged",
            },
        )
    ]

    r = client.get("/api/audit/events", headers=_admin_headers())

    assert r.json()["events"][0]["details"] == {
        "revoked_rows": 3,
        "ratio": 0.5,
        "big": 12345678901234567890,
        "nested": {"count": 1},
        "listed": [2, "text"],
        "flag": True,
        "text": "unchanged",
    }


def test_a_row_with_no_details_reports_none(store: _Storage) -> None:
    row = _row("evt-1")
    row.pop("details", None)
    store.rows = [row]

    r = client.get("/api/audit/events", headers=_admin_headers())

    assert r.json()["events"][0]["details"] is None


def test_a_non_map_details_attribute_reports_none_rather_than_500(
    store: _Storage,
) -> None:
    """Only ``put_audit_event`` writes this column; anything else is noise."""

    store.rows = [_row("evt-1", details="not a map")]

    r = client.get("/api/audit/events", headers=_admin_headers())

    assert r.status_code == 200
    assert r.json()["events"][0]["details"] is None


def test_non_string_attributes_are_coerced_not_500(store: _Storage) -> None:
    """A row from a future writer must not 500 the whole page."""

    row = _row("evt-1")
    row["event_id"] = Decimal("7")
    del row["created_at"]
    store.rows = [row]

    r = client.get("/api/audit/events", headers=_admin_headers())

    assert r.status_code == 200
    assert r.json()["events"][0]["event_id"] == "7"
    assert r.json()["events"][0]["created_at"] is None


def test_response_is_never_cached(store: _Storage) -> None:
    r = client.get("/api/audit/events", headers=_admin_headers())

    assert r.headers["cache-control"] == "no-store"


# ----------------------------------------------------------------
# Window derivation + the cap
# ----------------------------------------------------------------


def test_default_window_is_the_last_24_hours(store: _Storage) -> None:
    r = client.get("/api/audit/events", headers=_admin_headers())

    call = store.calls[0]
    from_dt = datetime.fromisoformat(call["from_iso"])
    to_dt = datetime.fromisoformat(call["to_iso"])
    assert to_dt - from_dt == timedelta(hours=24)
    # ``to`` defaults to now.
    assert abs((datetime.now(timezone.utc) - to_dt).total_seconds()) < 30
    assert r.json()["window"]["capped"] is False


def test_explicit_bounds_are_forwarded_normalised_to_utc(store: _Storage) -> None:
    r = client.get(
        "/api/audit/events",
        params={"from": "2026-08-01T00:00:00Z", "to": "2026-08-02T02:00:00+02:00"},
        headers=_admin_headers(),
    )

    call = store.calls[0]
    assert datetime.fromisoformat(call["from_iso"]) == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert datetime.fromisoformat(call["to_iso"]) == datetime(2026, 8, 2, tzinfo=timezone.utc)
    assert r.json()["window"]["from"].startswith("2026-08-01T00:00:00")


def test_filters_are_forwarded(store: _Storage) -> None:
    client.get(
        "/api/audit/events",
        params={"actor": "someone@test.com", "event_type": "auth.logout", "limit": 7},
        headers=_admin_headers(),
    )

    call = store.calls[0]
    assert call["actor_id"] == "someone@test.com"
    assert call["event_type"] == "auth.logout"
    assert call["limit"] == 7


@pytest.mark.parametrize("limit", [0, 101])
def test_limit_outside_the_allowed_range_is_rejected(store: _Storage, limit: int) -> None:
    r = client.get("/api/audit/events", params={"limit": limit}, headers=_admin_headers())

    assert r.status_code == 422


@pytest.mark.parametrize("field", ["from", "to"])
def test_unparseable_bound_is_a_400(store: _Storage, field: str) -> None:
    r = client.get("/api/audit/events", params={field: "yesterday"}, headers=_admin_headers())

    assert r.status_code == 400
    assert field in r.json()["detail"]


def test_inverted_window_is_a_400(store: _Storage) -> None:
    r = client.get(
        "/api/audit/events",
        params={"from": "2026-08-09T00:00:00Z", "to": "2026-08-01T00:00:00Z"},
        headers=_admin_headers(),
    )

    assert r.status_code == 400
    assert store.calls == []


def test_over_wide_window_is_capped_and_says_so(store: _Storage) -> None:
    """A compliance query must never be truncated silently."""

    r = client.get(
        "/api/audit/events",
        params={"from": "2020-01-01T00:00:00Z", "to": "2026-08-09T00:00:00Z"},
        headers=_admin_headers(),
    )

    window = r.json()["window"]
    assert window["capped"] is True
    assert window["requested_from"].startswith("2020-01-01")
    assert window["max_window_days"] == _MAX_WINDOW_DAYS
    # Served window is the tail of the requested range.
    assert datetime.fromisoformat(window["from"]) == datetime(
        2026, 8, 9, tzinfo=timezone.utc
    ) - timedelta(days=_MAX_WINDOW_DAYS)
    # ...and the storage layer only ever sees the capped bound.
    assert datetime.fromisoformat(store.calls[0]["from_iso"]) == datetime.fromisoformat(
        window["from"]
    )


def test_window_exactly_at_the_cap_is_not_capped(store: _Storage) -> None:
    to = datetime(2026, 8, 9, tzinfo=timezone.utc)
    r = client.get(
        "/api/audit/events",
        params={
            "from": (to - timedelta(days=_MAX_WINDOW_DAYS)).isoformat(),
            "to": to.isoformat(),
        },
        headers=_admin_headers(),
    )

    assert r.json()["window"]["capped"] is False


# ----------------------------------------------------------------
# Cursor pagination
# ----------------------------------------------------------------


def test_no_cursor_when_the_window_is_exhausted(store: _Storage) -> None:
    store.rows = [_row("evt-1")]
    store.next_cursor = None

    r = client.get("/api/audit/events", headers=_admin_headers())

    assert r.json()["next_cursor"] is None


def test_cursor_round_trips_the_resume_key_window_and_filters(
    store: _Storage,
) -> None:
    store.rows = [_row("evt-1")]
    store.next_cursor = {"PK": "AUDIT#2026-08-09#12", "SK": "1754740800#evt-1"}

    first = client.get(
        "/api/audit/events",
        params={
            "from": "2026-08-01T00:00:00Z",
            "to": "2026-08-09T00:00:00Z",
            "actor": "someone@test.com",
        },
        headers=_admin_headers(),
    )
    token = first.json()["next_cursor"]
    assert token is not None

    store.next_cursor = None
    second = client.get("/api/audit/events", params={"cursor": token}, headers=_admin_headers())

    assert second.status_code == 200
    resumed = store.calls[1]
    assert resumed["cursor"] == {
        "PK": "AUDIT#2026-08-09#12",
        "SK": "1754740800#evt-1",
    }
    # The window and the filter survive the round trip without the
    # client having to repeat either.
    assert resumed["actor_id"] == "someone@test.com"
    assert datetime.fromisoformat(resumed["from_iso"]) == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert datetime.fromisoformat(resumed["to_iso"]) == datetime(2026, 8, 9, tzinfo=timezone.utc)


def test_cursor_carries_the_requested_bound_so_the_cap_is_stable(
    store: _Storage,
) -> None:
    """Paging a capped window must not re-cap the already-capped bound."""

    store.next_cursor = {"PK": "AUDIT#2026-08-09#12", "SK": None}
    first = client.get(
        "/api/audit/events",
        params={"from": "2020-01-01T00:00:00Z", "to": "2026-08-09T00:00:00Z"},
        headers=_admin_headers(),
    )
    token = first.json()["next_cursor"]

    second = client.get("/api/audit/events", params={"cursor": token}, headers=_admin_headers())

    body = second.json()
    assert body["window"]["requested_from"].startswith("2020-01-01")
    assert body["window"]["capped"] is True
    assert store.calls[1]["from_iso"] == store.calls[0]["from_iso"]
    # A budget cursor resumes at the top of its partition.
    assert store.calls[1]["cursor"] == {"PK": "AUDIT#2026-08-09#12", "SK": None}


def test_cursor_matching_explicit_params_is_accepted(store: _Storage) -> None:
    r = client.get(
        "/api/audit/events",
        params={
            "cursor": _cursor(),
            "from": "2026-08-01T00:00:00Z",
            "to": "2026-08-09T00:00:00Z",
        },
        headers=_admin_headers(),
    )

    assert r.status_code == 200


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ("from", "2026-08-02T00:00:00Z"),
        ("to", "2026-08-08T00:00:00Z"),
        ("actor", "someone@test.com"),
        ("event_type", "auth.logout"),
    ],
)
def test_cursor_cannot_be_rebound_to_a_different_query(
    store: _Storage, param: str, value: str
) -> None:
    """Resuming under different filters would skip or repeat rows."""

    r = client.get(
        "/api/audit/events",
        params={"cursor": _cursor(), param: value},
        headers=_admin_headers(),
    )

    assert r.status_code == 400
    assert param in r.json()["detail"]
    assert store.calls == []


@pytest.mark.parametrize(
    "token",
    [
        "not-base64!!",
        base64.urlsafe_b64encode(b'"a string, not an object"').decode(),
        base64.urlsafe_b64encode(b"{}").decode(),
    ],
)
def test_malformed_cursor_is_a_400_not_a_500(store: _Storage, token: str) -> None:
    r = client.get("/api/audit/events", params={"cursor": token}, headers=_admin_headers())

    assert r.status_code == 400
    assert r.json()["detail"] == "invalid cursor"


@pytest.mark.parametrize(
    "overrides",
    [
        {"v": 99},
        {"pk": 12},
        {"from": None},
        {"to": None},
        {"sk": 5},
        {"actor": 5},
        {"event_type": 5},
    ],
)
def test_cursor_with_a_bad_field_is_a_400(store: _Storage, overrides: dict[str, Any]) -> None:
    r = client.get(
        "/api/audit/events",
        params={"cursor": _cursor(**overrides)},
        headers=_admin_headers(),
    )

    assert r.status_code == 400
    assert r.json()["detail"] == "invalid cursor"


@pytest.mark.parametrize(
    "pk",
    ["USER#someone", "AUDIT#not-a-date#12", "AUDIT#2026-13-40#25", "AUDIT#", "AUDIT"],
)
def test_a_well_typed_but_malformed_partition_is_still_a_400(store: _Storage, pk: str) -> None:
    """`pk` being a string is not enough — the shape has to parse here.

    ``storage._audit_shard_hour`` raises ``ValueError`` on a non-audit
    key and nothing downstream would translate that into a status code,
    so a type-only check would turn a hand-rolled cursor into a 500.
    """

    r = client.get(
        "/api/audit/events",
        params={"cursor": _cursor(pk=pk)},
        headers=_admin_headers(),
    )

    assert r.status_code == 400
    assert r.json()["detail"] == "invalid cursor"
    assert store.calls == []


@pytest.mark.parametrize("field", ["from", "to"])
def test_a_well_typed_but_unparseable_cursor_bound_is_still_a_400(
    store: _Storage, field: str
) -> None:
    """The handler parses these; a non-date string must not reach it."""

    r = client.get(
        "/api/audit/events",
        params={"cursor": _cursor(**{field: "yesterday"})},
        headers=_admin_headers(),
    )

    assert r.status_code == 400
    assert r.json()["detail"] == "invalid cursor"
    assert store.calls == []


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        ("sk", ("cursor", {"PK": "AUDIT#2026-08-09#12", "SK": None})),
        ("actor", ("actor_id", None)),
        ("event_type", ("event_type", None)),
    ],
)
def test_an_absent_optional_cursor_key_is_read_as_null_not_a_500(
    store: _Storage, missing: str, expected: tuple[str, Any]
) -> None:
    """Absent and null mean the same thing, and neither may crash.

    ``_optional_str`` passes an absent key — both shapes mean "no
    value" — so without normalisation the handler's later indexing
    raised ``KeyError`` and the client got a 500 for a merely-terse
    token.
    """

    r = client.get(
        "/api/audit/events",
        params={"cursor": _cursor(drop=(missing,))},
        headers=_admin_headers(),
    )

    assert r.status_code == 200
    key, value = expected
    assert store.calls[0][key] == value


# ----------------------------------------------------------------
# The read is itself audited — and that write is not best-effort
# ----------------------------------------------------------------


def test_a_served_read_writes_an_audit_row(store: _Storage) -> None:
    store.rows = [_row("evt-1"), _row("evt-2")]

    client.get(
        "/api/audit/events",
        params={"actor": "someone@test.com", "event_type": "auth.logout"},
        headers=_admin_headers(),
    )

    assert len(store.audit_events) == 1
    event = store.audit_events[0]
    assert event["event_type"] == "audit.read"
    assert event["actor_id"] == "admin@test.com"
    details = event["details"]
    assert details["role"] == "admin"
    assert details["actor_filter"] == "someone@test.com"
    assert details["event_type_filter"] == "auth.logout"
    assert details["returned"] == 2
    assert details["paged"] is False
    assert details["window_capped"] is False


def test_the_audit_row_records_the_question_never_the_answer(store: _Storage) -> None:
    """A compliance row must not become a second copy of the trail."""

    store.rows = [_row("evt-1", details={"secret": "value"})]

    client.get("/api/audit/events", headers=_admin_headers())

    serialised = json.dumps(store.audit_events[0])
    assert "evt-1" not in serialised
    assert "secret" not in serialised


def test_paged_and_capped_reads_are_recorded_as_such(store: _Storage) -> None:
    client.get(
        "/api/audit/events",
        params={
            "cursor": _cursor(**{"from": "2000-01-01T00:00:00+00:00"}),
        },
        headers=_admin_headers(),
    )

    details = store.audit_events[0]["details"]
    assert details["paged"] is True
    assert details["window_capped"] is True


def test_a_failed_audit_write_fails_the_request_rather_than_disclosing(
    store: _Storage,
) -> None:
    """Refusing to disclose is still available here — so it is taken."""

    store.rows = [_row("evt-1")]
    store.audit_raises = True

    with pytest.raises(RuntimeError, match="audit table unavailable"):
        client.get("/api/audit/events", headers=_admin_headers())


def test_a_rejected_request_writes_no_audit_row(store: _Storage) -> None:
    """Nothing was disclosed, so there is nothing to record."""

    client.get("/api/audit/events", params={"from": "nonsense"}, headers=_admin_headers())

    assert store.audit_events == []
