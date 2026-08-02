# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the in-process fixed-window rate limiter (#294).

The limiter is deliberately small, but three of its properties are
load-bearing for ``POST /auth/refresh`` and are asserted here rather than
only at the endpoint: rejected calls must not extend a window (or a burst
becomes a permanent lockout), ``rekey`` must carry a window across a
credential rotation (or a hard-rotating endpoint is unlimitable), and
memory must stay bounded under a flood of distinct keys.
"""

from __future__ import annotations

import pytest

from channel.rate_limit import DEFAULT_MAX_KEYS, FixedWindowRateLimiter, RateLimitDecision


class _Clock:
    """Manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


def _limiter(clock: _Clock, *, limit: int = 3, window: float = 60.0, max_keys: int = 100):
    return FixedWindowRateLimiter(
        limit=limit, window_seconds=window, max_keys=max_keys, clock=clock
    )


# ----------------------------------------------------------------
# Admission
# ----------------------------------------------------------------


def test_calls_under_the_limit_are_admitted(clock):
    limiter = _limiter(clock)
    assert [limiter.check("k").allowed for _ in range(3)] == [True, True, True]


def test_the_call_past_the_limit_is_rejected(clock):
    limiter = _limiter(clock)
    for _ in range(3):
        limiter.check("k")

    decision = limiter.check("k")

    assert decision.allowed is False
    assert decision.retry_after_seconds == 60


def test_keys_are_counted_independently(clock):
    """The whole point of a caller-chosen key: one abuser must not shed
    anyone else's traffic."""
    limiter = _limiter(clock)
    for _ in range(4):
        limiter.check("noisy")

    assert limiter.check("quiet").allowed is True


def test_the_window_rolls_over(clock):
    limiter = _limiter(clock)
    for _ in range(3):
        limiter.check("k")
    assert limiter.check("k").allowed is False

    clock.advance(60.0)

    assert limiter.check("k").allowed is True


def test_a_rejected_call_does_not_extend_the_window(clock):
    """Otherwise a client that keeps retrying holds its own window open
    forever and a one-second burst becomes a permanent lockout."""
    limiter = _limiter(clock)
    for _ in range(3):
        limiter.check("k")

    # Hammer the closed window right up to its final second.
    for _ in range(50):
        clock.advance(1.0)
        assert limiter.check("k").allowed is False

    clock.advance(10.0)  # 60s since the last ADMITTED call
    assert limiter.check("k").allowed is True


def test_retry_after_shrinks_as_the_window_drains(clock):
    limiter = _limiter(clock)
    for _ in range(3):
        limiter.check("k")

    clock.advance(45.0)

    assert limiter.check("k").retry_after_seconds == 15


def test_retry_after_never_advertises_zero(clock):
    """``Retry-After: 0`` invites an immediate retry that is certain to
    be rejected again."""
    limiter = _limiter(clock)
    for _ in range(3):
        limiter.check("k")

    clock.advance(59.999)

    assert limiter.check("k").retry_after_seconds == 1


# ----------------------------------------------------------------
# Kill switch
# ----------------------------------------------------------------


@pytest.mark.parametrize("limit", [0, -1])
def test_a_non_positive_limit_disables_the_limiter(clock, limit):
    limiter = _limiter(clock, limit=limit)

    assert limiter.enabled is False
    assert all(limiter.check("k").allowed for _ in range(100))


def test_a_positive_limit_reports_enabled(clock):
    assert _limiter(clock).enabled is True


# ----------------------------------------------------------------
# rekey — carrying a window across a credential rotation
# ----------------------------------------------------------------


def test_rekey_carries_the_count_to_the_successor(clock):
    """Without this a hard-rotating endpoint is unlimitable: every
    success mints a credential that hashes to a fresh key."""
    limiter = _limiter(clock)
    limiter.check("token-1")
    limiter.check("token-1")
    limiter.rekey("token-1", "token-2")

    assert limiter.check("token-2").allowed is True  # third in the window
    assert limiter.check("token-2").allowed is False  # fourth


def test_rekey_carries_the_window_start_not_just_the_count(clock):
    """A rotation must not silently restart the clock."""
    limiter = _limiter(clock)
    for _ in range(3):
        limiter.check("token-1")
    clock.advance(30.0)
    limiter.rekey("token-1", "token-2")

    assert limiter.check("token-2").retry_after_seconds == 30


def test_rekey_frees_the_old_key(clock):
    limiter = _limiter(clock)
    for _ in range(3):
        limiter.check("token-1")
    limiter.rekey("token-1", "token-2")

    # The spent credential's own bucket is gone, so re-presenting it
    # starts fresh — which is what lets reuse detection still fire on the
    # first replay instead of being masked by a throttle.
    assert limiter.check("token-1").allowed is True


def test_rekey_of_an_untracked_key_is_a_no_op(clock):
    limiter = _limiter(clock)

    limiter.rekey("never-seen", "successor")

    assert limiter.check("successor").allowed is True


# ----------------------------------------------------------------
# Memory bounds
# ----------------------------------------------------------------


def test_tracked_keys_are_capped(clock):
    """A flood of distinct junk credentials must not grow the dict
    without limit."""
    limiter = _limiter(clock, max_keys=5)

    for i in range(500):
        limiter.check(f"junk-{i}")

    assert len(limiter._buckets) == 5


def test_eviction_is_least_recently_used(clock):
    limiter = _limiter(clock, max_keys=2)
    limiter.check("a")
    limiter.check("b")
    limiter.check("a")  # touch a, so b is now the oldest

    limiter.check("c")

    assert set(limiter._buckets) == {"a", "c"}


def test_rekey_also_respects_the_cap(clock):
    limiter = _limiter(clock, max_keys=2)
    limiter.check("a")
    limiter.check("b")

    limiter.rekey("a", "c")  # a -> c, still 2 keys
    limiter.check("d")  # 3rd key, evicts the oldest

    assert len(limiter._buckets) == 2


def test_eviction_fails_open(clock):
    """Degrading toward ALLOWING is the deliberate direction — this is a
    cost damper, not an authorization control."""
    limiter = _limiter(clock, limit=1, max_keys=1)
    assert limiter.check("victim").allowed is True
    assert limiter.check("victim").allowed is False

    limiter.check("flood")  # evicts "victim"

    assert limiter.check("victim").allowed is True


def test_clear_drops_every_key(clock):
    limiter = _limiter(clock)
    for _ in range(3):
        limiter.check("k")

    limiter.clear()

    assert limiter.check("k").allowed is True


# ----------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------


def test_default_max_keys_is_applied_when_unspecified():
    limiter = FixedWindowRateLimiter(limit=1, window_seconds=1.0)
    assert limiter._max_keys == DEFAULT_MAX_KEYS


def test_the_default_clock_is_monotonic():
    """``time.time`` would let an NTP correction retroactively widen or
    collapse a live window."""
    import time

    limiter = FixedWindowRateLimiter(limit=1, window_seconds=1.0)
    assert limiter._clock is time.monotonic


def test_decision_is_immutable():
    """Callers pass decisions around; a mutable one invites a caller to
    'fix' a rejection in place."""
    decision = RateLimitDecision(allowed=False, retry_after_seconds=7)
    with pytest.raises(AttributeError):
        decision.allowed = True  # type: ignore[misc]
