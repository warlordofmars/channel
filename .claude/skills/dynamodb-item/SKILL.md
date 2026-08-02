---
name: dynamodb-item
description: Conventions for adding a new item type to the DynamoDB single-table design — PK/SK prefix taxonomy, TTL semantics, GSI naming, hour-shard pattern, and the CLAUDE.md update protocol.
status: full
triggers:
  paths:
    - "src/channel/storage.py"
    - "src/channel/models.py"
    - "src/channel/_table_schema.py"
  areas: []
---

# dynamodb-item

Adding a new item type to Channel's single-table DynamoDB design
follows a fixed set of conventions. Each is mechanically checkable
against the existing codebase; the canonical reference is
`src/channel/storage.py` (module-level read/write functions — there
is no wrapper class), the shared schema at
`src/channel/_table_schema.py` (attribute definitions + GSI shape,
used by DynamoDB Local and the integration suite), and the
production table declared in CDK at `infra/stacks/channel_stack.py`.

The conventions exist because single-table design only stays
discoverable when keys are predictable. A new prefix that doesn't
match the taxonomy means future agents have to scan the whole
table to find your items.

## 1. PK/SK pattern

All keys are prefixed strings. Prefix is the entity type, suffix
is the entity identifier:

```python
PK = "USER#alice-123"        # entity type # entity id
SK = "META"                  # singleton item per entity
```

Composite SKs are allowed when an entity owns a collection of
sub-items. The SK shape orders the collection lexicographically:

```python
PK = "LOG#2026-04-25#14"     # hour-sharded partition (see §4)
SK = "1745595600#evt-abc"    # {unix_timestamp}#{event_id}
```

The same PK prefix can carry several SK shapes — this is how a
single partition owns a heterogeneous collection. `USER#{user_id}`
holds `SK=META` (the user record), `SK=CHAT#{created_at}#{chat_id}`
(the Recents index), `SK=MCPSERVER#{server_id}`, and more;
`CHAT#{chat_id}` holds `SK=MSG#...`, `SK=ASSET#...`, and
`SK=MCPSERVERS#META`. Order every SK collection so a single
`begins_with` Query returns it.

Two patterns to avoid:

- **Unprefixed keys.** A bare `PK="alice-123"` collides with any
  other entity that happens to share that id space. Always use the
  `TYPE#id` form.
- **Hierarchical SKs without a fixed-width prefix.**
  `SK="2026-04-25T14:00:00Z#alice"` sorts correctly only because
  ISO-8601 is lexicographic. If you mix epoch ints and ISO strings
  in the same partition, sort order breaks. Pick one per partition
  and stick to it.

### 1.1 When the id IS a credential, hash it

Some row families are keyed by a secret the caller presents —
refresh tokens today, API keys or share links later. Those key off
the **SHA-256 hex digest**, never the secret itself:

```python
PK = f"REFRESH#{hashlib.sha256(raw_token.encode('utf-8')).hexdigest()}"
```

Lookup still works (the presenter supplies the secret, you hash it
and point-read), but a table dump, a PITR restore, a CloudWatch
export, or a stray log line yields nothing usable. Same instinct as
the app-layer KMS encryption on `MCPTOKEN#` rows, one notch stronger
— a hash has no key to leak.

Three rules:

- **Plain SHA-256, not a password KDF.** These inputs are ≥128 bits
  of CSPRNG output, so there is no dictionary to attack and bcrypt /
  argon2 would only add latency to a hot path. Reach for a KDF only
  when the input is human-chosen.
- **The plaintext exists in exactly one place: the return value of
  the mint helper.** Hand it to the client and drop it — no log
  line, no audit row, no sibling attribute.
  `storage.mint_refresh_token` is the reference implementation, and
  a unit test asserts the plaintext is absent from the rendered row.
- **Revoke by flag, not by delete.** Keep the row with a boolean and
  a reason. The tombstone is what makes reuse detection possible —
  a deleted row is indistinguishable from one that never existed
  (see the `REFRESH#` entry in §2).

## 2. Prefix taxonomy

Current item families in the single table (canonical list — mirrors
CLAUDE.md §"DynamoDB single table design", plus the `PREFS` and
`ATTACHMENT#` families that live in `src/channel/storage.py` but
aren't yet documented in CLAUDE.md; keep the two in sync):

| PK pattern | SK pattern | Purpose | TTL | Notes |
| --- | --- | --- | --- | --- |
| `LOG#{date}#{hour}` | `{timestamp}#{event_id}` | Activity log | no | Hour-sharded (§4). |
| `AUDIT#{date}#{hour}` | `{timestamp}#{event_id}` | Immutable compliance audit trail | yes | Hour-sharded (§4). TTL via `STARTER_AUDIT_RETENTION_DAYS` (default 365). |
| `USER#{user_id}` | `META` | User record | no | Would surface on `UserEmailIndex` via `GSI4PK=EMAIL#{email}`, but no current `src/channel/` code sets `GSI4PK` (the index is provisioned, not yet written). |
| `MGMT_STATE#{state}` | `META` | Google OAuth state parameter | yes | Short single-use TTL. |
| `DENY#{jti}` | `META` | JWT revocation denylist | yes | Point-read by the mgmt JWT's `jti`; written on `/auth/logout`; `ttl` = the denied token's own `exp` so the row self-prunes (#240). |
| `REFRESH#{sha256(raw_token)}` | `META` | Refresh-token row (one per token) | yes | **Raw token never persisted** — keyed by its SHA-256 digest (§1.1). Two expiry columns: `absolute_expires_at` (30d, fixed at login, carried forward unchanged by rotation) and `idle_expires_at` (7d, renewed per rotation); `ttl` = absolute expiry so a whole family prunes together. Optional `display_name` carries Google's `name` claim — available only at the OAuth callback — forward across rotations like `absolute_expires_at`, so a refreshed access token keeps the real name instead of degrading to the email local-part; omitted when absent, and pre-#292 rows simply read back `None` (#292). Hard rotation via conditional `update_item` (`attribute_exists(PK) AND #revoked = :live`) — that condition is what serializes concurrent consumes. Revoked ancestors are retained until TTL because re-presenting a `revoked_reason=rotated` row is the OAuth 2.1 reuse signal (RFC 9700 §4.14.2) and revokes the device's whole token-family. Projects onto `RefreshByUserIndex` via `GSI5PK`/`GSI5SK` (#290, epic #241). |
| `USER#{user_id}` | `CHAT#{created_at}#{chat_id}` | Chat-index row (one per chat) | no | Sortable so Recents is a single `Query(ScanIndexForward=False)`; projects onto `ChatByIdIndex`. |
| `CHAT#{chat_id}` | `MSG#{created_at}#{msg_id}` | Chat message row (one per turn) | no | UUID suffix avoids same-microsecond collisions across Lambda instances. |
| `IDEMP#{user_id}` | `{key}` | Idempotency reservation | yes | TTL = 1h after reserve; streaming POST replay short-circuit. |
| `USER#{user_id}` | `PREFS` | Per-user UI preferences | no | Single row per user; read/written by `get_prefs` / `put_prefs`. |
| `USER#{user_id}` | `ATTACHMENT#{att_id}` | Uploaded attachment metadata | no | Canonical attachment row (S3 object lives in the attachments bucket). |
| `USER#{user_id}` | `MCPSERVER#{server_id}` | Registered MCP server | no | DCR `client_id` + `tool_prefix` + `globally_enabled`; no GSI projection. |
| `USER#{user_id}` | `MCPTOKEN#{server_id}` | MCP OAuth tokens (sibling to MCPSERVER) | yes | Access/refresh tokens KMS-encrypted at the app layer; `ttl` = `expires_at + 30d` as an orphan-row upper bound. |
| `CHAT#{chat_id}` | `MCPSERVERS#META` | Per-chat MCP override | no | `mode=inherit` or `mode=explicit`. |
| `CHAT#{chat_id}` | `ASSET#{created_at}#{asset_id}` | Chat asset row | no | Mirrors `MSG#` so chat deletion cascades with one partition Query. Carries `owner_pk=ASSETOWNER#{owner}` / `owner_sk={created_at}#{asset_id}` projecting onto `AssetOwnerIndex`. Inline text ≤ 100 KB in `content`; larger text + binary in the assets bucket. |
| `EMAIL#{email}` | — (GSI key only, not a base PK) | Email → user lookup | n/a | The `GSI4PK` value that would project a `USER#` row onto `UserEmailIndex`. Provisioned but not written by any current `src/channel/` code; no item has `PK="EMAIL#..."` (a GSI query returns the underlying `USER#` row). |

Adding a new family:

1. Pick a `TYPE#id` PK and a `TYPE#...` SK shape that don't collide
   with the table above.
2. Decide TTL up-front — see §3.
3. If the item needs a secondary lookup path (by email, by owner,
   by chat id), pick or add a GSI per §5.
4. **Update CLAUDE.md §"DynamoDB single table design" in the
   same PR.** This is non-negotiable; the taxonomy table is the
   discovery contract for every future agent and stale entries
   silently break key derivation. See §7.

## 3. TTL semantics

The table has its TTL attribute set to `ttl` — configured in CDK at
`infra/stacks/channel_stack.py` and mirrored for local dev in
`src/channel/_table_schema.py` (`provision(...)` calls
`update_time_to_live` with `AttributeName="ttl"`). To enable expiry
on a new item type, set the `ttl` attribute on the item.

Two rules, both enforced by `code-reviewer`'s DynamoDB review
(check 8 — its example snippets still show the pre-fork layout, but
the `ttl` rules it enforces are current):

- **Attribute name is `ttl`** — lowercase, exact. The table config
  only honours that one name; setting `expires_at` or `TTL` is
  silently ignored by DynamoDB.
- **Value is a Unix timestamp integer.** Never an ISO-8601 string,
  never a `datetime` object — DynamoDB's TTL service reads
  unsigned integers and discards anything else. Compute via
  `int(time.time()) + ttl_seconds` or
  `int(expires_at.timestamp())`.

```python
import time

item = {
    "PK": f"DENY#{jti}",
    "SK": "META",
    "reason": "logout",
    # For DENY# the ttl is the denied token's own exp, so the row
    # self-prunes exactly when the token would have expired anyway.
    "ttl": int(token_exp),  # absolute Unix timestamp, integer
}
```

### Retention env-var pattern

Items whose retention is configurable (audit logs are the current
example) read the retention window from a per-item env var:

```python
retention_days = int(os.environ.get("STARTER_AUDIT_RETENTION_DAYS", "365"))
ttl_value = int(time.time()) + retention_days * 86400
```

When introducing a new retention-tunable item type, follow the
same `STARTER_<ITEM>_RETENTION_DAYS` env-var naming and document
the default in CLAUDE.md alongside the family entry.

## 4. Hour-shard pattern for log items

`LOG#` and `AUDIT#` partition by `{date}#{hour}` — UTC date plus
zero-padded hour — to avoid hot partitions during traffic spikes.
A single date partition would funnel every event in 24 hours into
one DynamoDB partition; sharding by hour spreads writes across
24 partitions per day.

Canonical PK/SK for log-type items:

```python
from datetime import datetime, timezone

now = datetime.now(timezone.utc)
PK = f"LOG#{now:%Y-%m-%d}#{now:%H}"      # e.g. "LOG#2026-04-25#14"
SK = f"{int(now.timestamp())}#{event_id}"  # e.g. "1745595600#evt-abc"
```

When querying a time range, fan out: enumerate the hours in the
range and issue one `query` per partition. Don't try to scan
across hours with `begins_with(PK, "LOG#2026-04-25")` — a `Scan`
with that filter pulls every partition and defeats the shard.

Use the hour-shard pattern for any new item type that:

- Carries time-series semantics (events, requests, audit
  entries)
- Has bursty write traffic that would otherwise cluster on one
  partition

For low-volume time-series data (e.g. one event per user per
day), a daily shard is fine — pick the granularity that keeps
each partition under DynamoDB's 1000 WCU / 3000 RCU limit at
peak.

## 5. GSI naming and the GSIxPK convention

The table defines six GSIs. The five numbered indexes name their
partition/sort attributes by slot (`GSI1PK`…`GSI5PK`); the asset
index uses named attributes. The authoritative shape lives in
`src/channel/_table_schema.py` (mirrored into
`infra/stacks/channel_stack.py` for production):

| GSI | Partition key attr | Sort key attr | Purpose |
| --- | --- | --- | --- |
| `KeyIndex` (GSI1) | `GSI1PK` | `GSI1SK` | Generic secondary index; not projected by any current row family. |
| `TagIndex` (GSI2) | `GSI2PK` | `GSI2SK` | Generic secondary index; not projected by any current row family. |
| `ChatByIdIndex` (GSI3) | `GSI3PK` | `GSI3SK` | Direct chat-id → chat-index-row lookup. Chat-index rows set `GSI3PK=CHAT_ID#{chat_id}`, `GSI3SK=META`. Sparse. |
| `UserEmailIndex` (GSI4) | `GSI4PK` | — | User lookup by email. **Provisioned but not yet written by any `src/channel/` code** — a writer would set `GSI4PK=EMAIL#{email}` on the `USER#` row to project it. |
| `RefreshByUserIndex` (GSI5) | `GSI5PK` | `GSI5SK` | A user's refresh rows — per-device family revoke, sign-out-everywhere, and the #293 sessions list. Refresh rows set `GSI5PK=REFRESH_USER#{user_id}`, `GSI5SK={issued_at}#{token_hash[:16]}`. Sparse. |
| `AssetOwnerIndex` | `owner_pk` | `owner_sk` | Cross-chat asset browse, newest first. Asset rows set `owner_pk=ASSETOWNER#{owner}`, `owner_sk={created_at}#{asset_id}`. Sparse. |

Slot numbering is **not** declaration order — `ChatByIdIndex` holds
the GSI3 slot while being declared fourth in both files, and
`AssetOwnerIndex` skipped the numbering entirely (which is why GSI5
was still free when #290 needed it). Read the attribute names; never
infer a slot from position.

To put an item on a GSI, set the matching attribute(s) on the item:

```python
# User item — how you'd project a USER# row onto UserEmailIndex.
# GSI4PK is an indexing-only attribute; no current storage code writes
# it (UserEmailIndex is provisioned but unused today).
item = {
    "PK": f"USER#{user_id}",
    "SK": "META",
    "GSI4PK": f"EMAIL#{email}",   # set this to surface the row on UserEmailIndex
    ...
}

# Asset row — also queryable on AssetOwnerIndex (sort-keyed, newest first)
item = {
    "PK": f"CHAT#{chat_id}",
    "SK": f"ASSET#{created_at}#{asset_id}",
    "owner_pk": f"ASSETOWNER#{owner}",
    "owner_sk": f"{created_at}#{asset_id}",
    ...
}
```

Conventions:

- **GSI naming.** `<Domain>Index` (PascalCase, "Index" suffix).
  Existing examples: `ChatByIdIndex`, `UserEmailIndex`,
  `AssetOwnerIndex`, `RefreshByUserIndex`.
- **Numbered slots by default.** Take the next free
  `GSI{n}PK` / `GSI{n}SK` pair unless the attribute name itself
  carries meaning — `AssetOwnerIndex` uses `owner_pk`/`owner_sk`
  because the settled #321 design makes the workspace-tenancy
  migration a one-attribute swap (§8). That's the exception, not
  the pattern.
- **Sparse indexes are fine.** Items without the matching GSI key
  attribute simply do not appear on that GSI. This is the standard
  way to scope an index to a subset of item types (only chat-index
  rows carry `GSI3PK`; only refresh rows carry `GSI5PK`; only asset
  rows carry `owner_pk`).
- **GSI reads are eventually consistent — always, unavoidably.**
  DynamoDB rejects `ConsistentRead=True` on an index query, so a row
  written moments ago may not be projected yet. Never build a
  correctness- or security-critical invariant on "the index returned
  everything": a base-table point read (`ConsistentRead=True`) is the
  only strongly-consistent access this table has. Where a security
  path has to sweep an index anyway — `storage._revoke_refresh_family`
  is the live example — say so in the docstring, bound the damage, and
  don't let the caller believe the sweep is exhaustive.
- **A `FilterExpression` often beats a second index.** When the
  narrower query runs over a small partition, filter rather than
  add a GSI: `RefreshByUserIndex` keys on `user_id` alone and
  filters on `device_id`, which leaves the sort key free to be
  `{issued_at}#{hash prefix}` — time-ordered for the sessions list,
  and one index instead of two.
- **Adding a new GSI is an infra change — in two places, plus two
  assertions.** Declare the index in **both**
  `src/channel/_table_schema.py` (so DynamoDB Local and the
  integration suite get it) **and** `infra/stacks/channel_stack.py`
  (so the deployed table gets it); assert its key schema in
  `tests/unit/test_table_schema.py` and
  `tests/unit/test_channel_stack.py`; then add the storage code
  that writes the GSI keys. Declaring it in only one of the two
  files yields a suite that passes locally and a
  `ValidationException` in AWS, or the reverse.
- **New GSI → a real schema migration.** CloudFormation applies at
  most one GSI addition per stack update, and backfill is
  asynchronous: the index sits in `CREATING` for a while after the
  deploy reports success. Never ship a read path that assumes the
  index is queryable the instant the stack update finishes, and
  coordinate via a dedicated PR when prod data exists.

## 6. Table name source

Storage code reads the table name from the environment. The
project-specific env var is `STARTER_TABLE_NAME` (the `STARTER_*`
prefix scopes config across the codebase). `_get_table()` in
`src/channel/storage.py` reads it as a **required** variable — no
silent default; an unset value is a `KeyError` at first use:

```python
import os

import boto3

def _get_table():
    table_name = os.environ["STARTER_TABLE_NAME"]      # required
    endpoint = os.environ.get("DYNAMODB_ENDPOINT")      # set for DynamoDB Local
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    ddb = boto3.resource("dynamodb", region_name=region, endpoint_url=endpoint)
    return ddb.Table(table_name)
```

Wired across the project at:

- `infra/stacks/channel_stack.py` — Lambda environment sets
  `STARTER_TABLE_NAME` to the per-env table name
- `tests/integration/conftest.py` — sets the var before importing
  storage (provisions `channel-test` via `channel._table_schema`)
- `scripts/reset_dev_table.py` — recreates the local `channel`
  table (`TABLE = "channel"`) after each `inv dev` restart

Never hardcode the table name — `code-reviewer` check 8 enforces
this. The runtime contract is `STARTER_TABLE_NAME`. Some older docs
(including `code-reviewer.md`) still refer to a bare `TABLE_NAME`,
but that name is **not** set by the CDK stack or the test fixtures —
production code and tests must read `STARTER_TABLE_NAME`, and code
that reads only `TABLE_NAME` will fail at runtime.

The endpoint URL is also env-driven (`DYNAMODB_ENDPOINT`) so tests
point at DynamoDB Local without code changes. `_get_table()`
resolves all three at *call time*, not import time, which keeps
test fixtures isolated from each other.

## 7. Update CLAUDE.md in the same PR

Adding a new item type means updating two places in the same PR:

1. The prefix table in CLAUDE.md §"DynamoDB single table design"
2. This skill's §2 prefix taxonomy table

Both updates ride with the storage code that introduces the new
item. The taxonomy table is the discovery contract for every
future agent — a stale entry means the next agent that adds an
item won't see your prefix and may collide with it.

If the new item type also adds a GSI or a retention env var,
extend §5 or §3 of this skill and the matching CLAUDE.md
sections in the same PR.

## 8. Workspace tenancy (anticipated single-attribute migration)

Per the **"workspaces are the tenancy root"** product decision
(CLAUDE.md §"Product decisions"), multi-tenancy consumes the
workspace model rather than introducing a second tenancy axis. The
data model is already shaped for this: the asset row's `owner`
(encoded as `owner_pk=ASSETOWNER#{owner}`) is `user_id` today and
becomes `f"{workspace_id}/{user_id}"` when workspaces land — a
**single-attribute migration, never a second tenancy field**. The
AgentCore Memory `actorId` follows the same shape
(`f"{workspace_id}/{user_id}"`, slash-separated — CLAUDE.md
§"AgentCore Memory").

**Status: anticipated, not active.** Don't add a separate
`workspace_id` column to existing item types in advance — when
workspaces ship, the tenancy prefix folds into the existing
`owner` / `actorId` value. Update this section and CLAUDE.md once
the workspace primitive is real.

## See also

- [`example.py`](./example.py) — copy-pasteable item shape for a
  new family, covering PK/SK, TTL, GSI keys, and the table-name
  resolution pattern.
- CLAUDE.md §"DynamoDB single table design" — current family
  taxonomy (must stay in sync with §2 above).
- `src/channel/_table_schema.py` — shared attribute definitions +
  GSI shape (DynamoDB Local + integration suite).
- `infra/stacks/channel_stack.py` — production table + GSI
  definitions (CDK).
- `.claude/agents/code-reviewer.md` §8 — review-time enforcement
  of the conventions above.
- CLAUDE.md §"Product decisions" ("workspaces are the tenancy
  root") — the workspace-tenancy rationale behind §8.
