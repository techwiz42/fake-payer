"""Playing a block of PCM16 8 kHz audio to the caller at the pace it is heard, so a hang-up right after it does not cut it off."""
from __future__ import annotations

import asyncio

FRAME_BYTES = 320                       # 20 ms of PCM16 at 8 kHz
FRAME_SECONDS = 0.02


async def play_pcm(call, pcm: bytes) -> None:
    for start in range(0, len(pcm), FRAME_BYTES):
        await call.send_audio(pcm[start:start + FRAME_BYTES])
        await asyncio.sleep(FRAME_SECONDS)
