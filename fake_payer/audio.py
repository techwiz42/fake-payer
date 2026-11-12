"""Playing a block of PCM16 8 kHz audio to the caller at the pace it is heard, so a hang-up right after it does not cut it off."""
from __future__ import annotations

import asyncio
import math
import struct

FRAME_BYTES = 320                       # 20 ms of PCM16 at 8 kHz
FRAME_SECONDS = 0.02


async def play_pcm(call, pcm: bytes) -> None:
    for start in range(0, len(pcm), FRAME_BYTES):
        await call.send_audio(pcm[start:start + FRAME_BYTES])
        await asyncio.sleep(FRAME_SECONDS)


SAMPLE_RATE = 8000
RING_SECONDS = 1.0                      # one ring of the tone
RING_GAP_SECONDS = 2.0                  # silence between rings


def ringback_pcm(rings: int) -> bytes:
    """What a caller hears while the other end is ringing: the North American ringback tone (440 Hz and 480 Hz together), `rings` times, with the silence between the rings
    (none after the last). Empty for no rings."""
    if rings <= 0:
        return b""
    tone = bytearray()
    for n in range(int(RING_SECONDS * SAMPLE_RATE)):
        t = n / SAMPLE_RATE
        edge = min(1.0, n / 80, (int(RING_SECONDS * SAMPLE_RATE) - n) / 80)          # a few milliseconds of fade so the tone does not click
        value = int(6000 * edge * (math.sin(2 * math.pi * 440 * t) + math.sin(2 * math.pi * 480 * t)) / 2)
        tone += struct.pack("<h", value)
    gap = bytes(int(RING_GAP_SECONDS * SAMPLE_RATE) * 2)
    return gap.join([bytes(tone)] * rings)
