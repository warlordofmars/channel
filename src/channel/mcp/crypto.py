# Copyright (c) 2026 John Carter. All rights reserved.
"""KMS encrypt/decrypt for MCP token blobs.

OAuth bearer tokens are small (<= 1 KB typically). Direct KMS
``encrypt``/``decrypt`` is sufficient — no envelope-encryption dance.
The KMS key ARN comes from ``CHANNEL_MCP_TOKEN_KMS_KEY_ID``; the CDK
stack creates a dedicated CMK with rotation enabled and grants the API
Lambda's role ``kms:Encrypt`` + ``kms:Decrypt`` on it.
"""

from __future__ import annotations

import functools
import os
from typing import Any


@functools.lru_cache(maxsize=1)
def _get_kms_client() -> Any:  # pragma: no cover - patched in tests
    import boto3  # noqa: PLC0415

    return boto3.client("kms", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))


# Local-dev sentinel: when CHANNEL_MCP_TOKEN_KMS_KEY_ID is the literal
# ``"local"`` string, encrypt/decrypt short-circuit to a passthrough
# wrapping. This lets ``inv dev`` run the MCP flows against DynamoDB
# Local without touching AWS KMS. Production envs must never set this
# value — the assertion test in test_channel_stack.py guards against it
# leaking, and the CDK stack always sets a real KMS key ARN.
_LOCAL_DEV_SENTINEL = "local"


def _key_id() -> str:
    key_id = os.environ.get("CHANNEL_MCP_TOKEN_KMS_KEY_ID")
    if not key_id:
        raise RuntimeError(
            "CHANNEL_MCP_TOKEN_KMS_KEY_ID is unset; MCP token storage requires "
            "a KMS key for application-layer encryption."
        )
    return key_id


def encrypt_blob(plaintext: str) -> bytes:
    """Encrypt a UTF-8 token string with the configured CMK."""
    key_id = _key_id()
    if key_id == _LOCAL_DEV_SENTINEL:
        return ("LOCAL::" + plaintext).encode("utf-8")
    client = _get_kms_client()
    resp = client.encrypt(KeyId=key_id, Plaintext=plaintext.encode("utf-8"))
    return bytes(resp["CiphertextBlob"])


def decrypt_blob(ciphertext: bytes) -> str:
    """Decrypt a ciphertext blob and return the original UTF-8 token."""
    key_id = _key_id()
    if key_id == _LOCAL_DEV_SENTINEL:
        return ciphertext.decode("utf-8").removeprefix("LOCAL::")
    client = _get_kms_client()
    resp = client.decrypt(CiphertextBlob=ciphertext)
    return bytes(resp["Plaintext"]).decode("utf-8")
