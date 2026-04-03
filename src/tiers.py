from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class Tier:
    rate_limit_bps: int
    max_epochs_per_minute: int
    max_queue_bytes: int


class TokenBucket:
    def __init__(self, rate_bps: int, capacity: int):
        self.rate = rate_bps
        self.capacity = capacity
        self.tokens = capacity
        self.t = time.monotonic()

    def consume(self, n: int) -> bool:
        if self.rate <= 0 or self.capacity <= 0:
            return True
        now = time.monotonic()
        dt = now - self.t
        self.t = now
        self.tokens = min(self.capacity, self.tokens + dt * self.rate)
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def time_to_available(self, n: int) -> float:
        if self.rate <= 0 or self.capacity <= 0:
            return 0.0
        now = time.monotonic()
        dt = now - self.t
        self.t = now
        self.tokens = min(self.capacity, self.tokens + dt * self.rate)
        if self.tokens >= n:
            return 0.0
        missing = float(n) - float(self.tokens)
        return max(missing / float(self.rate), 0.0)


def build_tier(name: str, data: Dict[str, Any]) -> Tier:
    rl = int(data.get("rate_limit_bps", 0) or 0)
    ep = int(data.get("max_epochs_per_minute", 0) or 0)
    mq = int(data.get("max_queue_bytes", 0) or 0)
    return Tier(rate_limit_bps=rl, max_epochs_per_minute=ep, max_queue_bytes=mq)


def epoch_gate_ok(last_ts: Optional[float], epm: int, now: float) -> bool:
    if epm <= 0:
        return True
    period = 60.0 / float(epm)
    if last_ts is None:
        return True
    return (now - last_ts) >= period
