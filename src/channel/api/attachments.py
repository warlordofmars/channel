# Copyright (c) 2026 John Carter. All rights reserved.
"""Attachments API (#175) — presigned upload + finalize.

Two endpoints:

* ``POST /api/attachments/presign`` — validate the upload, return a
  presigned S3 PUT URL plus a short-lived JWT (``typ=att_presign``)
  that the client passes back at finalize. The presigned URL forces
  ``x-amz-tagging: unreferenced=1`` so the bucket's lifecycle rule
  (see CDK stack) GCs the object if finalize never lands.

* ``POST /api/attachments`` — verify the JWT, HEAD the S3 object to
  confirm the upload happened and the size matches the claim, write
  the canonical ``Attachment`` row, then flip the ``unreferenced``
  tag from ``1`` to ``0`` so the lifecycle rule no longer targets the
  object. Tag-flip failure is logged + swallowed (canonical row is
  authoritative).

Presign-claim shape (stateless JWT, mgmt secret, HS256, 5-min TTL):

    typ: "att_presign"
    sub: <user_id>
    att_id, name, mime, size_bytes, s3_bucket, s3_key
    exp: <unix ts>

Rate limiting is out of scope for v1 — the codebase has no rate-limit
infrastructure yet. Tracked separately as a follow-up issue.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from channel import storage
from channel.api._auth import require_mgmt_user
from channel.auth.tokens import ISSUER, JWT_ALGORITHM, _jwt_secret
from channel.models import Attachment
from channel.storage import _get_s3_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attachments", tags=["attachments"])

# v1 allowlist — maps to Strands' DocumentFormat + ImageFormat literals so
# Bedrock can natively parse every accepted MIME without a server-side
# conversion step.
ALLOWED_MIMES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "text/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/markdown",
    }
)

MAX_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB per epic #109 decision
MAX_NAME_LEN = 256
PRESIGN_TTL_SECONDS = 300  # 5 min
PRESIGN_TOKEN_TYPE = "att_presign"


class PresignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_LEN)
    mime: str
    size_bytes: int = Field(gt=0, le=MAX_SIZE_BYTES)


class FinalizeRequest(BaseModel):
    presign_token: str
    checksum_sha256: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@router.post("/presign")
async def presign(
    payload: PresignRequest,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Return a presigned PUT URL + a JWT that finalize will redeem."""

    if payload.mime not in ALLOWED_MIMES:
        raise HTTPException(status_code=400, detail="Unsupported MIME type")

    att_id = str(uuid4())
    user_id = claims["sub"]
    bucket = os.environ["STARTER_ATTACHMENTS_BUCKET"]
    key = f"attachments/user/{user_id}/{att_id}"

    url = _get_s3_client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ContentType": payload.mime,
            # Tag at upload time so the bucket lifecycle rule GCs orphans.
            # Finalize flips this to ``unreferenced=0`` to spare the
            # canonical object from cleanup.
            "Tagging": "unreferenced=1",
            "ServerSideEncryption": "aws:kms",
        },
        ExpiresIn=PRESIGN_TTL_SECONDS,
    )

    now = int(time.time())
    presign_token = jwt.encode(
        {
            "iss": ISSUER,
            "typ": PRESIGN_TOKEN_TYPE,
            "sub": user_id,
            "att_id": att_id,
            "name": payload.name,
            "mime": payload.mime,
            "size_bytes": payload.size_bytes,
            "s3_bucket": bucket,
            "s3_key": key,
            "iat": now,
            "exp": now + PRESIGN_TTL_SECONDS,
        },
        _jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )

    required_headers = {
        "Content-Type": payload.mime,
        "x-amz-tagging": "unreferenced=1",
        "x-amz-server-side-encryption": "aws:kms",
    }

    return {
        "att_id": att_id,
        "url": url,
        "required_headers": required_headers,
        "presign_token": presign_token,
    }


@router.post("")
async def finalize(
    payload: FinalizeRequest,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Verify the upload landed, write the canonical row, flip the tag."""

    try:
        claim = jwt.decode(
            payload.presign_token,
            _jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            issuer=ISSUER,
        )
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid presign token") from exc

    if claim.get("typ") != PRESIGN_TOKEN_TYPE or claim.get("sub") != claims["sub"]:
        # Token belongs to a different user, or a different token-type was
        # presented (e.g. the mgmt JWT itself). Treat both as auth failures
        # rather than 400 — leaking the distinction helps token-replay attempts.
        raise HTTPException(status_code=401, detail="Invalid presign token")

    s3 = _get_s3_client()
    bucket = claim["s3_bucket"]
    key = claim["s3_key"]

    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NotFound", "NoSuchKey"}:
            # Object never made it to S3 (upload aborted or expired URL).
            raise HTTPException(status_code=410, detail="Uploaded object not found") from exc
        # Permissions / transient / unknown S3 error — surface as upstream.
        # Mirror storage.verify_attachment_object's fallback so an empty
        # code doesn't render the trailing-colon "S3 error: ".
        detail = f"S3 error: {code}" if code else "S3 error"
        raise HTTPException(status_code=502, detail=detail) from exc

    if head.get("ContentLength") != claim["size_bytes"]:
        # Hard reject — client could otherwise presign for 1KB and upload 20MB.
        raise HTTPException(
            status_code=400,
            detail="Uploaded size does not match presign claim",
        )

    att = Attachment(
        id=claim["att_id"],
        user_id=claims["sub"],
        name=claim["name"],
        mime=claim["mime"],
        size_bytes=claim["size_bytes"],
        s3_key=key,
        s3_bucket=bucket,
        checksum_sha256=payload.checksum_sha256,
        created_at=_now_iso(),
    )
    storage.put_attachment(att)

    # Flip the lifecycle tag — bucket should no longer GC this object.
    # Failure is logged but doesn't fail the finalize; the canonical row
    # is the source of truth, and the worst case is the bucket's
    # ``unreferenced=1`` lifecycle GCs the object after 24h if no
    # message ever references it (orphan recovery is then a re-upload).
    try:
        s3.put_object_tagging(
            Bucket=bucket,
            Key=key,
            Tagging={"TagSet": [{"Key": "unreferenced", "Value": "0"}]},
        )
    except ClientError as exc:
        logger.warning(
            "attachment.tag_flip_failed att_id=%s",
            claim["att_id"],
            extra={"error_type": type(exc).__name__, "error_message": str(exc)},
            exc_info=True,
        )

    return att.model_dump(mode="json")
