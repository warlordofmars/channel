# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for KMS token-blob crypto."""

from __future__ import annotations

from typing import Any

import pytest

from channel.mcp import crypto


class _FakeKMS:
    """Round-trippable fake KMS. ``encrypt`` prepends a sentinel; ``decrypt`` strips it."""

    def __init__(self) -> None:
        self.last_key_id: str | None = None

    def encrypt(self, **kwargs: Any) -> dict[str, Any]:
        self.last_key_id = kwargs["KeyId"]
        return {"CiphertextBlob": b"enc:" + kwargs["Plaintext"]}

    def decrypt(self, **kwargs: Any) -> dict[str, Any]:
        blob: bytes = kwargs["CiphertextBlob"]
        if not blob.startswith(b"enc:"):
            raise ValueError("not encrypted by this fake")
        return {"Plaintext": blob[4:]}


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    crypto._get_kms_client.cache_clear()


@pytest.fixture
def fake_kms(monkeypatch: pytest.MonkeyPatch) -> _FakeKMS:
    kms = _FakeKMS()
    monkeypatch.setattr(crypto, "_get_kms_client", lambda: kms)
    monkeypatch.setenv("STARTER_MCP_TOKEN_KMS_KEY_ID", "alias/test")
    return kms


def test_round_trip(fake_kms: _FakeKMS) -> None:
    plaintext = "the-bearer-token"
    ciphertext = crypto.encrypt_blob(plaintext)
    assert isinstance(ciphertext, bytes)
    assert plaintext != ciphertext.decode("latin-1")
    assert crypto.decrypt_blob(ciphertext) == plaintext


def test_encrypt_uses_configured_key(fake_kms: _FakeKMS) -> None:
    crypto.encrypt_blob("payload")
    assert fake_kms.last_key_id == "alias/test"


def test_missing_key_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STARTER_MCP_TOKEN_KMS_KEY_ID", raising=False)
    with pytest.raises(RuntimeError, match="STARTER_MCP_TOKEN_KMS_KEY_ID"):
        crypto.encrypt_blob("payload")


def test_local_dev_sentinel_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """When STARTER_MCP_TOKEN_KMS_KEY_ID is literally 'local', encrypt/
    decrypt short-circuit to a passthrough wrapping — used by ``inv dev``."""
    monkeypatch.setenv("STARTER_MCP_TOKEN_KMS_KEY_ID", "local")
    enc = crypto.encrypt_blob("plain")
    assert enc == b"LOCAL::plain"
    assert crypto.decrypt_blob(enc) == "plain"
