"""Limits on volume and spend for a line anyone can dial.

Kept by the program, in memory, never trusted to the model: simultaneous calls, calls per caller per hour (calls with no number share one count), and a daily spend ceiling
on an estimate (call minutes at a configured cost per minute, covering telephony, speech and model). A call refused here costs nothing beyond the connection itself.
"""
from __future__ import annotations

import datetime
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple

from .config import Config

HOUR = 3600.0


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: Optional[str] = None        # "spend" | "busy" | "rate"


@dataclass
class Ticket:
    caller_key: str
    started: float


class Limits:
    def __init__(self, config: Config, clock=time.time):
        self._config = config
        self._clock = clock
        self._active: Dict[int, Ticket] = {}
        self._admitted: Dict[str, Deque[float]] = defaultdict(deque)
        self._spent_today = 0.0
        self._day = self._today()
        self._next_id = 0

    def _today(self) -> datetime.date:
        return datetime.datetime.fromtimestamp(self._clock(), datetime.timezone.utc).date()

    def _roll_day(self) -> None:
        if self._today() != self._day:
            self._day, self._spent_today = self._today(), 0.0

    def _cost(self, seconds: float) -> float:
        return max(0.0, seconds) / 60.0 * self._config.cost_per_minute_usd

    def spend_today(self) -> float:
        """Spend so far today: finished calls plus the running cost of the calls in progress."""
        self._roll_day()
        now = self._clock()
        return self._spent_today + sum(self._cost(now - t.started) for t in self._active.values())

    @property
    def active_calls(self) -> int:
        return len(self._active)

    def admit(self, caller_key: str) -> Tuple[Decision, Optional[int]]:
        """Decide whether a call may start. Returns the decision and, when allowed, a handle to pass to `release` when the call ends."""
        now = self._clock()
        if self.spend_today() >= self._config.daily_spend_ceiling_usd:
            return Decision(False, "spend"), None
        if len(self._active) >= self._config.max_concurrent_calls:
            return Decision(False, "busy"), None
        recent = self._admitted[caller_key]
        while recent and now - recent[0] >= HOUR:
            recent.popleft()
        if len(recent) >= self._config.max_calls_per_number_per_hour:
            return Decision(False, "rate"), None
        recent.append(now)                       # only a call that is let in counts: refused attempts do not lengthen a caller's wait
        self._next_id += 1
        self._active[self._next_id] = Ticket(caller_key, now)
        return Decision(True), self._next_id

    def release(self, handle: Optional[int]) -> None:
        ticket = self._active.pop(handle, None) if handle is not None else None
        if ticket is not None:
            self._roll_day()
            self._spent_today += self._cost(self._clock() - ticket.started)
