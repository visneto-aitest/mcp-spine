"""
MCP Spine — Rate Limiter

Sliding-window rate limiting per tool and globally.
Prevents runaway agent loops from exhausting downstream servers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RateLimitBucket:
    """Sliding window rate limit for a single key."""
    max_calls: int
    window_seconds: float
    timestamps: list[float] = field(default_factory=list)

    def allow(self) -> bool:
        """Check if a call is allowed, and record it if so."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        if len(self.timestamps) >= self.max_calls:
            return False
        self.timestamps.append(now)
        return True

    @property
    def remaining(self) -> int:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        active = sum(1 for t in self.timestamps if t > cutoff)
        return max(0, self.max_calls - active)


class RateLimiter:
    """Per-tool rate limiting with configurable defaults."""

    def __init__(
        self,
        default_max_calls: int = 30,
        default_window: float = 60.0,
        overrides: dict[str, tuple[int, float]] | None = None,
    ):
        self._default_max = default_max_calls
        self._default_window = default_window
        self._buckets: dict[str, RateLimitBucket] = {}
        self._overrides = overrides or {}

    def check(self, tool_name: str) -> bool:
        """Return True if the tool call is allowed."""
        if tool_name not in self._buckets:
            max_calls, window = self._overrides.get(
                tool_name, (self._default_max, self._default_window)
            )
            self._buckets[tool_name] = RateLimitBucket(max_calls, window)
        return self._buckets[tool_name].allow()

    def remaining(self, tool_name: str) -> int:
        bucket = self._buckets.get(tool_name)
        return bucket.remaining if bucket else self._default_max
