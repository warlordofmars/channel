# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the #175 attachments endpoints: presign + finalize.

S3 is mocked at the ``channel.storage._get_s3_client`` seam; DDB writes
are stubbed via ``channel.storage.put_attachment``.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

os.environ.setdefault("STARTER_JWT_SECRET", "test-secret-for-unit-tests")
os.environ.setdefault("STARTER_ATTACHMENTS_BUCKET", "channel-attachments-test")
os.environ.setdefault("STARTER_TABLE_NAME", "channel-test")

from botocore.exceptions import ClientError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt as jose_jwt  # noqa: E402

from channel.api._auth import require_mgmt_user  # noqa: E402
from channel.api.main import app  # noqa: E402


class _FakeS3:
    """Stub the boto3 S3 client surface the router touches."""

    def __init__(self) -> None:
        self.presigned: list[dict[str, Any]] = []
        self.heads: dict[tuple[str, str], dict[str, Any]] = {}
        self.head_errors: dict[tuple[str, str], str] = {}
        self.tag_calls: list[dict[str, Any]] = []
        self.tag_errors: dict[tuple[str, str], str] = {}

    def generate_presigned_url(
        self, ClientMethod: str, *, Params: dict[str, Any], ExpiresIn: int
    ) -> str:
        self.presigned.append({"method": ClientMethod, "params": Params, "ttl": ExpiresIn})
        return f"https://s3.example/{Params['Bucket']}/{Params['Key']}?sig=stub"

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        code = self.head_errors.get((Bucket, Key))
        if code:
            raise ClientError({"Error": {"Code": code, "Message": code}}, "HeadObject")
        if (Bucket, Key) not in self.heads:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return self.heads[(Bucket, Key)]

    def put_object_tagging(
        self, *, Bucket: str, Key: str, Tagging: dict[str, Any]
    ) -> dict[str, Any]:
        code = self.tag_errors.get((Bucket, Key))
        if code:
            raise ClientError({"Error": {"Code": code, "Message": code}}, "PutObjectTagging")
        self.tag_calls.append({"Bucket": Bucket, "Key": Key, "Tagging": Tagging})
        return {}


@pytest.fixture
def fake_s3(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    fake = _FakeS3()
    monkeypatch.setattr("channel.api.attachments._get_s3_client", lambda: fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    def _stub_user() -> dict[str, Any]:
        return {"sub": "u-1", "role": "user"}

    app.dependency_overrides[require_mgmt_user] = _stub_user
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def stub_put_attachment(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Capture put_attachment calls without touching DDB."""

    captured: list[Any] = []
    monkeypatch.setattr(
        "channel.api.attachments.storage.put_attachment",
        lambda att: captured.append(att),
    )
    return captured


def _presign(client: TestClient, **overrides: Any) -> dict[str, Any]:
    body = {"name": "spec.pdf", "mime": "application/pdf", "size_bytes": 12345}
    body.update(overrides)
    resp = client.post("/api/attachments/presign", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---- presign ----------------------------------------------------


def test_presign_happy_path_returns_url_and_token(client: TestClient, fake_s3: _FakeS3) -> None:
    out = _presign(client)
    assert set(out) == {"att_id", "url", "required_headers", "presign_token"}
    assert out["url"].startswith("https://s3.example/")
    # Bucket + key fed into the presigner
    params = fake_s3.presigned[0]["params"]
    assert params["Bucket"] == "channel-attachments-test"
    assert params["Key"] == f"attachments/user/u-1/{out['att_id']}"
    assert params["ContentType"] == "application/pdf"
    assert params["Tagging"] == "unreferenced=1"
    assert params["ServerSideEncryption"] == "aws:kms"
    # 5-minute TTL
    assert fake_s3.presigned[0]["ttl"] == 300
    # Required headers mirror what the browser must send
    assert out["required_headers"]["Content-Type"] == "application/pdf"
    assert out["required_headers"]["x-amz-tagging"] == "unreferenced=1"
    assert out["required_headers"]["x-amz-server-side-encryption"] == "aws:kms"
    # Decoded presign_token carries the claims finalize will verify
    claim = jose_jwt.decode(
        out["presign_token"], os.environ["STARTER_JWT_SECRET"], algorithms=["HS256"]
    )
    assert claim["typ"] == "att_presign"
    assert claim["sub"] == "u-1"
    assert claim["att_id"] == out["att_id"]
    assert claim["mime"] == "application/pdf"
    assert claim["size_bytes"] == 12345
    assert claim["s3_bucket"] == "channel-attachments-test"
    assert claim["s3_key"] == params["Key"]


def test_presign_rejects_unknown_mime(client: TestClient, fake_s3: _FakeS3) -> None:
    resp = client.post(
        "/api/attachments/presign",
        json={"name": "evil.bin", "mime": "application/octet-stream", "size_bytes": 1},
    )
    assert resp.status_code == 400
    assert "MIME" in resp.json()["detail"]


def test_presign_rejects_oversize_via_pydantic(client: TestClient, fake_s3: _FakeS3) -> None:
    resp = client.post(
        "/api/attachments/presign",
        json={
            "name": "big.pdf",
            "mime": "application/pdf",
            "size_bytes": 20 * 1024 * 1024 + 1,
        },
    )
    assert resp.status_code == 422  # Pydantic field constraint


def test_presign_rejects_zero_or_negative_size(client: TestClient, fake_s3: _FakeS3) -> None:
    for bad in (0, -1):
        resp = client.post(
            "/api/attachments/presign",
            json={"name": "x.pdf", "mime": "application/pdf", "size_bytes": bad},
        )
        assert resp.status_code == 422


def test_presign_rejects_oversized_name(client: TestClient, fake_s3: _FakeS3) -> None:
    resp = client.post(
        "/api/attachments/presign",
        json={
            "name": "x" * 257,
            "mime": "application/pdf",
            "size_bytes": 1,
        },
    )
    assert resp.status_code == 422


def test_presign_accepts_each_allowlisted_mime(client: TestClient, fake_s3: _FakeS3) -> None:
    for mime in (
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "text/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/markdown",
    ):
        resp = client.post(
            "/api/attachments/presign",
            json={"name": "f", "mime": mime, "size_bytes": 1},
        )
        assert resp.status_code == 200, f"{mime} rejected"


# ---- finalize ---------------------------------------------------


def _good_claim(*, sub: str = "u-1", **overrides: Any) -> str:
    """Build a valid presign-claim JWT for finalize tests."""

    claim: dict[str, Any] = {
        "typ": "att_presign",
        "sub": sub,
        "att_id": "att-fin-1",
        "name": "spec.pdf",
        "mime": "application/pdf",
        "size_bytes": 12345,
        "s3_bucket": "channel-attachments-test",
        "s3_key": f"attachments/user/{sub}/att-fin-1",
        "exp": int(time.time()) + 60,
    }
    claim.update(overrides)
    return jose_jwt.encode(claim, os.environ["STARTER_JWT_SECRET"], algorithm="HS256")


def test_finalize_happy_path_writes_row_and_flips_tag(
    client: TestClient, fake_s3: _FakeS3, stub_put_attachment: list[Any]
) -> None:
    token = _good_claim()
    fake_s3.heads[("channel-attachments-test", "attachments/user/u-1/att-fin-1")] = {
        "ContentLength": 12345
    }

    resp = client.post(
        "/api/attachments",
        json={"presign_token": token, "checksum_sha256": "sha-abc"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "att-fin-1"
    assert body["user_id"] == "u-1"
    assert body["checksum_sha256"] == "sha-abc"
    # Canonical row written
    assert len(stub_put_attachment) == 1
    att = stub_put_attachment[0]
    assert att.id == "att-fin-1"
    assert att.s3_key == "attachments/user/u-1/att-fin-1"
    # Tag flipped
    assert fake_s3.tag_calls == [
        {
            "Bucket": "channel-attachments-test",
            "Key": "attachments/user/u-1/att-fin-1",
            "Tagging": {"TagSet": [{"Key": "unreferenced", "Value": "0"}]},
        }
    ]


def test_finalize_rejects_invalid_token(
    client: TestClient, fake_s3: _FakeS3, stub_put_attachment: list[Any]
) -> None:
    resp = client.post(
        "/api/attachments",
        json={"presign_token": "not.a.valid.jwt", "checksum_sha256": "x"},
    )
    assert resp.status_code == 401
    assert stub_put_attachment == []


def test_finalize_rejects_cross_user_token(
    client: TestClient, fake_s3: _FakeS3, stub_put_attachment: list[Any]
) -> None:
    """A token signed for a different user must not be redeemable by the
    authenticated caller."""

    token = _good_claim(sub="u-other")
    resp = client.post(
        "/api/attachments",
        json={"presign_token": token, "checksum_sha256": "x"},
    )
    assert resp.status_code == 401
    assert stub_put_attachment == []


def test_finalize_rejects_wrong_token_type(
    client: TestClient, fake_s3: _FakeS3, stub_put_attachment: list[Any]
) -> None:
    """An mgmt-typed JWT must not be redeemable as a presign token."""

    token = jose_jwt.encode(
        {"typ": "mgmt", "sub": "u-1", "exp": int(time.time()) + 60},
        os.environ["STARTER_JWT_SECRET"],
        algorithm="HS256",
    )
    resp = client.post(
        "/api/attachments",
        json={"presign_token": token, "checksum_sha256": "x"},
    )
    assert resp.status_code == 401


def test_finalize_returns_410_when_s3_object_missing(
    client: TestClient, fake_s3: _FakeS3, stub_put_attachment: list[Any]
) -> None:
    token = _good_claim()
    # fake_s3 has no head registered → 404 ClientError
    resp = client.post(
        "/api/attachments",
        json={"presign_token": token, "checksum_sha256": "x"},
    )
    assert resp.status_code == 410
    assert stub_put_attachment == []


def test_finalize_returns_502_on_unexpected_s3_error(
    client: TestClient, fake_s3: _FakeS3, stub_put_attachment: list[Any]
) -> None:
    """Permissions or transient S3 failures don't map to user error —
    surface as upstream-failure 502."""

    token = _good_claim()
    fake_s3.head_errors[("channel-attachments-test", "attachments/user/u-1/att-fin-1")] = (
        "InternalError"
    )
    resp = client.post(
        "/api/attachments",
        json={"presign_token": token, "checksum_sha256": "x"},
    )
    assert resp.status_code == 502


def test_finalize_rejects_size_mismatch(
    client: TestClient, fake_s3: _FakeS3, stub_put_attachment: list[Any]
) -> None:
    """The actual uploaded size must match the presign claim — else the
    user could presign for 1KB and upload 20MB."""

    token = _good_claim()
    fake_s3.heads[("channel-attachments-test", "attachments/user/u-1/att-fin-1")] = {
        "ContentLength": 99999
    }
    resp = client.post(
        "/api/attachments",
        json={"presign_token": token, "checksum_sha256": "x"},
    )
    assert resp.status_code == 400
    assert stub_put_attachment == []


def test_finalize_swallows_tag_flip_failure(
    client: TestClient, fake_s3: _FakeS3, stub_put_attachment: list[Any]
) -> None:
    """If PutObjectTagging fails after the row is written, the cascade
    succeeds — the bucket's unreferenced=1 will GC the object after 24h
    if no message ever references it, but the authoritative row is
    already in place."""

    token = _good_claim()
    fake_s3.heads[("channel-attachments-test", "attachments/user/u-1/att-fin-1")] = {
        "ContentLength": 12345
    }
    fake_s3.tag_errors[("channel-attachments-test", "attachments/user/u-1/att-fin-1")] = (
        "AccessDenied"
    )

    resp = client.post(
        "/api/attachments",
        json={"presign_token": token, "checksum_sha256": "x"},
    )
    assert resp.status_code == 200
    assert len(stub_put_attachment) == 1


def test_finalize_rejects_expired_token(
    client: TestClient, fake_s3: _FakeS3, stub_put_attachment: list[Any]
) -> None:
    expired = jose_jwt.encode(
        {
            "typ": "att_presign",
            "sub": "u-1",
            "att_id": "att-fin-1",
            "name": "spec.pdf",
            "mime": "application/pdf",
            "size_bytes": 12345,
            "s3_bucket": "channel-attachments-test",
            "s3_key": "attachments/user/u-1/att-fin-1",
            "exp": int(time.time()) - 1,
        },
        os.environ["STARTER_JWT_SECRET"],
        algorithm="HS256",
    )
    resp = client.post(
        "/api/attachments",
        json={"presign_token": expired, "checksum_sha256": "x"},
    )
    assert resp.status_code == 401


# ---- router mount ----------------------------------------------


def test_attachments_router_is_mounted(client: TestClient) -> None:
    """Mount sanity check — the router is reachable under /api/attachments."""

    resp = client.post("/api/attachments/presign", json={})
    # Should be a validation error (422), NOT a 404 not-found.
    assert resp.status_code != 404
