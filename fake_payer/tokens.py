"""Who may open the audio websocket: only a call that came through the answer request.

The answer request is protected by a secret in its URL (the ANSWER_KEY given to the telephony provider). When it answers a call it signs a short-lived token over the call's
id and the caller's masked identity, and puts it in the websocket address it returns. The websocket refuses anything without a valid, unexpired token, so nobody can run a
conversation (and spend money on the speech and model services) by connecting to it directly.

Caller numbers are never kept: a number becomes a keyed hash (for counting calls per number) and its last four digits (for the log).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence
from urllib.parse import urlencode

TOKEN_SECONDS = 60
WITHHELD = "withheld"
_NO_NUMBER = {"", "anonymous", "unknown", "restricted", "private", "unavailable", "blocked"}


@dataclass(frozen=True)
class Caller:
    key: str            # keyed hash of the number: calls from the same number share it; the number cannot be recovered from it
    last4: str          # shown in the log


class CallTokens:
    def __init__(self, answer_key: str, clock=time.time):
        self._secret = answer_key.encode()
        self._salt = secrets.token_bytes(16)          # fresh each start: the per-number counts live in memory too
        self._clock = clock

    def caller(self, number: Optional[str]) -> Caller:
        digits = "".join(c for c in (number or "") if c.isdigit())
        if (number or "").strip().lower() in _NO_NUMBER or not digits:
            return Caller(WITHHELD, "none")
        return Caller(hmac.new(self._salt, digits.encode(), hashlib.sha256).hexdigest()[:16], digits[-4:])

    def _sign(self, call_uuid: str, caller: Caller, expires: int) -> str:
        message = f"{call_uuid}|{caller.key}|{caller.last4}|{expires}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()[:32]

    def issue(self, call_uuid: str, caller: Caller) -> str:
        """The query string for the websocket address."""
        expires = int(self._clock()) + TOKEN_SECONDS
        return urlencode({"c": caller.key, "l": caller.last4, "e": expires, "t": self._sign(call_uuid, caller, expires)})

    def verify(self, call_uuid: str, query: Mapping[str, Sequence[str]]) -> Optional[Caller]:
        """The caller the token was issued for, or None when it is missing, altered, for another call, or expired."""
        try:
            key, last4, expires, token = (query[name][0] for name in ("c", "l", "e", "t"))
            expires_at = int(expires)
        except (KeyError, IndexError, ValueError):
            return None
        caller = Caller(key, last4)
        if not hmac.compare_digest(token, self._sign(call_uuid, caller, expires_at)) or self._clock() > expires_at:
            return None
        return caller


def key_matches(supplied: Optional[str], answer_key: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied.encode(), answer_key.encode())
