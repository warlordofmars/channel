# Copyright (c) 2026 John Carter. All rights reserved.
"""In-process fixed-window rate limiting (#294, epic #241).

The app had no application-layer rate limiter before this module. The
only throttle in front of it is the WAF ``GlobalRateLimit`` rule in
``infra/stacks/channel_stack.py`` — 1 000 requests / 5 min **per IP**,
which is why ``api/csp.py`` describes itself as "per-IP rate-limited".
That rule is deliberately coarse and deliberately per-IP, and neither
property suits a credential endpoint:

- **Per-IP is the wrong key for authenticated-ish traffic.** Carrier NAT
  and corporate egress put thousands of unrelated users behind one
  address. A tight per-IP limit on ``POST /auth/refresh`` would sign out
  everyone in an office because one machine looped — the self-inflicted
  DoS this module exists to avoid.
- **1 000 / 5 min is far above the useful ceiling** for an endpoint a
  healthy client calls roughly once an hour.

So: a per-credential in-process limiter, layered *under* WAF rather than
replacing it.

Design notes
------------
**Fixed window, not a token bucket or sliding log.** A fixed window is
two numbers per key and no background sweep. Its known imprecision — up
to ``2 × limit`` across a window boundary — is irrelevant here, where the
job is bounding a runaway retry loop, not metering a paid quota.

**Per-process, therefore best-effort.** Lambda gives every concurrent
instance its own copy, so the effective ceiling is ``limit × instances``.
That is acceptable for this endpoint specifically: hard rotation means a
device has exactly one live refresh token at a time, so a *legitimate*
client cannot fan its refreshes out across instances — it must wait for
each successor before it can present one. A DynamoDB- or Redis-backed
counter would be exact, at the cost of adding a write to the very path
whose write volume this is meant to bound.

**Only admitted requests count.** A rejected request does not extend the
window. Without that rule a client that keeps retrying holds its own
window open forever and a one-second burst becomes a permanent lockout —
again, exactly the self-DoS shape. Recovery is guaranteed within
``window_seconds`` of the last *admitted* call.

**A per-credential key bounds repetition, not volume.** A caller keyed
on the credential it presents is limited in how often it may re-present
*the same* one; a spray of *distinct* junk credentials is not bounded by
this limiter at all, since each one gets its own fresh bucket. That is
inherent to the key choice and accepted — WAF's per-IP
``GlobalRateLimit`` is the volumetric backstop, and the credential check
behind this is what makes a wrong guess worthless. It does mean
``RefreshRateLimited`` must never be read as coverage against credential
stuffing: it measures repetition, and a stuffing run would leave it flat.

**Bounded memory, failing open.** ``max_keys`` caps the tracked-key set
with LRU eviction, so an attacker presenting a flood of distinct junk
credentials cannot grow the dict without limit. Eviction resets an
evicted key's counter, i.e. the limiter degrades toward *allowing*
traffic rather than denying it. That direction is chosen deliberately:
this is a cost/abuse damper, not an authorization control, and WAF plus
the credential check behind it are what actually stop an attacker.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

# Default ceiling on distinct keys held per process. Each entry is a
# short string key plus a two-field tuple, so 10k entries is a couple of
# MB — comfortably inside a Lambda's memory while being far more keys
# than any legitimate traffic mix produces.
DEFAULT_MAX_KEYS = 10_000


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of one :meth:`FixedWindowRateLimiter.check` call.

    ``retry_after_seconds`` is meaningful only when ``allowed`` is
    ``False``; it is the whole seconds remaining in the current window,
    floored at 1 so a caller never emits ``Retry-After: 0`` (which
    invites an immediate retry that is certain to be rejected again).
    """

    allowed: bool
    retry_after_seconds: int


_ALLOWED = RateLimitDecision(allowed=True, retry_after_seconds=0)


class FixedWindowRateLimiter:
    """Count admitted calls per key inside a rolling fixed window.

    Not a general-purpose middleware: the caller chooses the key, which
    is what lets ``auth/refresh.py`` scope its limit to a single
    token-family rather than to an IP address.
    """

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configure the limiter.

        Args:
            limit: Admitted calls allowed per key per window. ``0`` or
                negative disables the limiter entirely — the kill switch
                an operator reaches for if the limit turns out to be
                wrong in production.
            window_seconds: Window length.
            max_keys: LRU ceiling on tracked keys.
            clock: Monotonic time source, injectable for tests.
                ``time.monotonic`` rather than ``time.time`` so a clock
                step (NTP correction, VM resume) cannot retroactively
                widen or collapse a window.
        """
        self._limit = limit
        self._window = window_seconds
        self._max_keys = max_keys
        self._clock = clock
        # Insertion order is LRU order: every touch moves its key to the
        # end, so the oldest-touched key is always at the front.
        self._buckets: OrderedDict[str, tuple[float, int]] = OrderedDict()
        # The endpoint handler is ``async`` and does not await inside a
        # check, so the event loop alone would serialize these mutations
        # today. The lock is here so a future sync (threadpool-executed)
        # caller cannot silently make the counter racy.
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """Whether the limiter admits anything other than everything."""
        return self._limit > 0

    def check(self, key: str) -> RateLimitDecision:
        """Admit or reject one call against ``key``'s window.

        Increments only on admission — see the module docstring for why
        a rejected call must not extend the window.
        """
        if not self.enabled:
            return _ALLOWED

        now = self._clock()
        with self._lock:
            started_at, count = self._buckets.get(key, (now, 0))
            if now - started_at >= self._window:
                started_at, count = now, 0

            if count >= self._limit:
                # Keep the window's original start so the client's wait
                # shrinks as it approaches the boundary.
                self._buckets[key] = (started_at, count)
                self._buckets.move_to_end(key)
                remaining = self._window - (now - started_at)
                return RateLimitDecision(
                    allowed=False,
                    retry_after_seconds=max(1, math.ceil(remaining)),
                )

            self._buckets[key] = (started_at, count + 1)
            self._buckets.move_to_end(key)
            self._evict()
            return _ALLOWED

    def rekey(self, old_key: str, new_key: str) -> None:
        """Carry ``old_key``'s window and count over to ``new_key``.

        Needed by any caller whose key is a rotating credential. Without
        it a hard-rotating endpoint is unlimitable: every successful call
        hands the client a fresh credential, which hashes to a fresh key,
        which starts a fresh window — so an unbounded loop of *successful*
        calls would never trip a limit keyed on the credential.

        A missing ``old_key`` is a no-op (it may have been evicted, or
        the limiter may be disabled).
        """
        with self._lock:
            bucket = self._buckets.pop(old_key, None)
            if bucket is None:
                return
            self._buckets[new_key] = bucket
            self._buckets.move_to_end(new_key)
            self._evict()

    def clear(self) -> None:
        """Drop all tracked keys. Test hook; never called in production."""
        with self._lock:
            self._buckets.clear()

    def _evict(self) -> None:
        """Trim to ``max_keys``, oldest-touched first. Caller holds the lock."""
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
