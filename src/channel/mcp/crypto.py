# Copyright (c) 2026 John Carter. All rights reserved.
"""KMS encrypt/decrypt for MCP token blobs.

OAuth bearer tokens are small (<= 1 KB typically). Direct KMS
``encrypt``/``decrypt`` is sufficient — no envelope-encryption dance.
The KMS key ARN comes from ``STARTER_MCP_TOKEN_KMS_KEY_ID``; the CDK
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


def _key_id() -> str:
    key_id = os.environ.get("STARTER_MCP_TOKEN_KMS_KEY_ID")
    if not key_id:
        raise RuntimeError(
            "STARTER_MCP_TOKEN_KMS_KEY_ID is unset; MCP token storage requires "
            "a KMS key for application-layer encryption."
        )
    return key_id


def encrypt_blob(plaintext: str) -> bytes:
    """Encrypt a UTF-8 token string with the configured CMK."""
    client = _get_kms_client()
    resp = client.encrypt(KeyId=_key_id(), Plaintext=plaintext.encode("utf-8"))
    return bytes(resp["CiphertextBlob"])


def decrypt_blob(ciphertext: bytes) -> str:
    """Decrypt a ciphertext blob and return the original UTF-8 token."""
    client = _get_kms_client()
    resp = client.decrypt(CiphertextBlob=ciphertext)
    return bytes(resp["Plaintext"]).decode("utf-8")
