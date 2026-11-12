"""The line rings before it answers: the ringback tone a caller hears, and that it comes ahead of the greeting."""
import asyncio
import math
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

from fake_payer import audio
from fake_payer.bridge import BridgeSettings, VoiceBridge
from fake_payer.config import Config


def samples(pcm):
    return struct.unpack("<%dh" % (len(pcm) // 2), pcm)


def test_no_rings_is_no_audio():
    assert audio.ringback_pcm(0) == b""


def test_each_ring_is_a_second_of_tone_with_two_seconds_of_silence_between_and_none_after():
    pcm = audio.ringback_pcm(2)
    assert len(pcm) == 2 * (1 * 8000 + 0) * 2 + 2 * 8000 * 2                      # two rings and one gap, 16-bit samples at 8 kHz
    values = samples(pcm)
    assert max(abs(v) for v in values[:8000]) > 3000                              # the first ring is audible
    assert all(v == 0 for v in values[8000:24000])                                # the gap is silent
    assert max(abs(v) for v in values[24000:]) > 3000                             # the second ring
    assert abs(values[0]) < 100 and abs(values[-1]) < 100                         # it starts and ends without a click


def test_the_tone_is_the_north_american_ringback_of_440_and_480_hertz():
    values = samples(audio.ringback_pcm(1))[:8000]
    def power(freq):
        re = sum(v * math.cos(2 * math.pi * freq * n / 8000) for n, v in enumerate(values))
        im = sum(v * math.sin(2 * math.pi * freq * n / 8000) for n, v in enumerate(values))
        return re * re + im * im
    assert power(440) > 20 * power(1000) and power(480) > 20 * power(1000)


def test_two_rings_are_the_default_and_zero_turns_them_off():
    env = {"SONIOX_API_KEY": "k", "ANTHROPIC_API_KEY": "m", "FAKE_PAYER_NUMBER": "+12025550123", "ANSWER_KEY": "x" * 32}
    assert Config.from_env(env).ring_count == 2
    assert Config.from_env({**env, "RING_COUNT": "0"}).ring_count == 0


@pytest.mark.asyncio
async def test_the_ringing_is_played_before_the_greeting_is_spoken():
    events = []
    bridge = VoiceBridge(BridgeSettings(call_uuid="c", greeting="hi", soniox_api_key="k", ring_audio=b"r" * 640), {}, "persona")

    async def connect():
        bridge._tts = MagicMock(synthesize=AsyncMock(side_effect=lambda text: events.append("greeting") or "s1"))
    bridge._connect_legs = connect
    call = MagicMock(send_audio=AsyncMock(side_effect=lambda frame: events.append("ring")))
    bridge._vonage_to_stt = AsyncMock(return_value=None)
    bridge._stt_event_loop = AsyncMock(return_value=None)
    bridge._tts_event_loop = AsyncMock(return_value=None)
    bridge._watchdog = AsyncMock(return_value=None)
    try:
        await asyncio.wait_for(bridge.run(call), 3)
    except Exception:
        pass
    assert events[:2] == ["ring", "ring"] and events.index("greeting") > events.index("ring")
