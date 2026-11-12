"""Configuration, read from the environment.

Credentials and the telephone number come only from the environment, never from a file in this repository. The program refuses to start when one is
missing, and no value of a credential is ever printed, logged or put in an error: messages name the variable, not its value.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Mapping, Optional

REQUIRED = ("SONIOX_API_KEY", "ANTHROPIC_API_KEY", "FAKE_PAYER_NUMBER", "ANSWER_KEY")
_SECRETS = ("soniox_api_key", "anthropic_api_key", "answer_key")


class ConfigError(Exception):
    """The configuration cannot be used. The message names variables, never values."""


def _answer_key(raw: str) -> str:
    key = raw.strip()
    if len(key) < 16 or not key.replace("-", "").replace("_", "").isalnum():
        raise ConfigError("ANSWER_KEY must be at least 16 letters, digits, dashes or underscores")
    return key


@dataclass(frozen=True)
class Config:
    soniox_api_key: str
    anthropic_api_key: str
    number: str                                   # the line's own number
    answer_key: str                               # a secret in the answer URL given to Vonage: only requests carrying it are answered
    max_concurrent_calls: int = 3
    max_calls_per_number_per_hour: int = 120
    max_call_seconds: int = 300
    daily_spend_ceiling_usd: float = 5.0
    log_retention_days: int = 7
    call_log_dir: str = "calls"
    tts_voice: str = "Adrian"
    stt_model: str = "stt-rt-preview"
    listen_host: str = "127.0.0.1"
    port: int = 8080
    cost_per_minute_usd: float = 0.15
    audio_cache_dir: str = "audio_cache"
    tts_model: str = "tts-rt-v1"
    reply_delay_seconds: float = 2.0              # the least time from the caller finishing to the first sound of the reply
    answer_delay_seconds: float = 2.0             # silence between the last ring and the greeting
    ring_count: int = 2                           # rings the caller hears before the line answers; 0 answers at once

    def __repr__(self) -> str:
        shown = {f.name: ("<set>" if f.name in _SECRETS else getattr(self, f.name)) for f in fields(self)}
        return "Config(" + ", ".join(f"{k}={v!r}" for k, v in shown.items()) + ")"

    __str__ = __repr__

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "Config":
        env = os.environ if environ is None else environ
        missing = [name for name in REQUIRED if not (env.get(name) or "").strip()]
        if missing:
            raise ConfigError("missing required configuration: " + ", ".join(missing))

        raw_rings = (env.get("RING_COUNT") or "").strip() or "2"
        if not raw_rings.isdigit() or int(raw_rings) > 6:
            raise ConfigError("RING_COUNT must be a whole number from 0 (answer at once) to 6")
        ring_count = int(raw_rings)

        raw_delay = (env.get("ANSWER_DELAY_SECONDS") or "").strip() or "2"
        try:
            answer_delay = float(raw_delay)
        except ValueError:
            raise ConfigError("ANSWER_DELAY_SECONDS is not a valid number") from None
        if not 0 <= answer_delay <= 10:
            raise ConfigError("ANSWER_DELAY_SECONDS must be from 0 to 10")

        raw_reply = (env.get("REPLY_DELAY_SECONDS") or "").strip() or "2"
        try:
            reply_delay = float(raw_reply)
        except ValueError:
            raise ConfigError("REPLY_DELAY_SECONDS is not a valid number") from None
        if not 0 <= reply_delay <= 10:
            raise ConfigError("REPLY_DELAY_SECONDS must be from 0 to 10")

        def number(name: str, default, kind):
            raw = (env.get(name) or "").strip()
            if not raw:
                return default
            try:
                value = kind(raw)
            except ValueError:
                raise ConfigError(f"{name} is not a valid {kind.__name__}") from None
            if value <= 0:
                raise ConfigError(f"{name} must be greater than zero")
            return value

        return cls(
            soniox_api_key=env["SONIOX_API_KEY"].strip(),
            anthropic_api_key=env["ANTHROPIC_API_KEY"].strip(),
            number=env["FAKE_PAYER_NUMBER"].strip(),
            answer_key=_answer_key(env["ANSWER_KEY"]),
            max_concurrent_calls=number("MAX_CONCURRENT_CALLS", 3, int),
            max_calls_per_number_per_hour=number("MAX_CALLS_PER_NUMBER_PER_HOUR", 120, int),
            max_call_seconds=number("MAX_CALL_SECONDS", 300, int),
            daily_spend_ceiling_usd=number("DAILY_SPEND_CEILING_USD", 5.0, float),
            log_retention_days=number("LOG_RETENTION_DAYS", 7, int),
            call_log_dir=(env.get("CALL_LOG_DIR") or "calls").strip(),
            tts_voice=(env.get("TTS_VOICE") or "Adrian").strip(),
            stt_model=(env.get("SONIOX_STT_MODEL") or "stt-rt-preview").strip(),
            listen_host=(env.get("LISTEN_HOST") or "127.0.0.1").strip(),
            port=number("PORT", 8080, int),
            cost_per_minute_usd=number("COST_PER_MINUTE_USD", 0.15, float),
            audio_cache_dir=(env.get("AUDIO_CACHE_DIR") or "audio_cache").strip(),
            tts_model=(env.get("SONIOX_TTS_MODEL") or "tts-rt-v1").strip(),
            ring_count=ring_count,
            answer_delay_seconds=answer_delay,
            reply_delay_seconds=reply_delay,
        )
