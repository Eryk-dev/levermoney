"""
Global rate limiter for Conta Azul API.
CA limit: 600 req/min, 10 req/s.
We use 9 req/s burst and 540 req/min guard (90% of limits).

Shared between CaWorker and ca_api reads so all CA traffic
goes through a single token bucket.
"""
import asyncio
import time
import logging

logger = logging.getLogger(__name__)


class TokenBucket:
    """Async token bucket rate limiter with per-second and per-minute guards."""

    def __init__(self, rate_per_sec: float = 9.0, max_per_min: int = 540):
        self._rate = rate_per_sec
        self._max_per_min = max_per_min

        # Token bucket state
        self._tokens = rate_per_sec
        self._max_tokens = rate_per_sec
        self._last_refill = time.monotonic()

        # Per-minute sliding window
        self._minute_timestamps: list[float] = []

        self._lock = asyncio.Lock()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._max_tokens, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def _prune_minute_window(self, now: float):
        cutoff = now - 60.0
        while self._minute_timestamps and self._minute_timestamps[0] < cutoff:
            self._minute_timestamps.pop(0)

    async def acquire(self):
        """Wait until a token is available, then consume it."""
        while True:
            async with self._lock:
                now = time.monotonic()
                self._refill()
                self._prune_minute_window(now)

                if self._tokens >= 1.0 and len(self._minute_timestamps) < self._max_per_min:
                    self._tokens -= 1.0
                    self._minute_timestamps.append(now)
                    return

                # Calculate wait time
                if self._tokens < 1.0:
                    wait = (1.0 - self._tokens) / self._rate
                else:
                    # Minute guard hit — wait until oldest entry expires
                    wait = self._minute_timestamps[0] + 60.0 - now

            await asyncio.sleep(max(wait, 0.01))


# Singleton instance shared across the application
rate_limiter = TokenBucket()
