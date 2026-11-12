"""What the line says when it cannot take a call, in the configured voice, without using a speech service per call.

Each phrase is synthesized once with Soniox and kept (in memory and in a cache directory), so a refused call plays stored audio. If a phrase cannot be synthesized the line
still refuses, in silence.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Dict

from .config import Config
from .soniox_streaming_client import SonioxTTSClient, TTSAudioChunk, TTSError, TTSSynthesisComplete

logger = logging.getLogger(__name__)

PHRASES: Dict[str, str] = {
    "busy": "I'm sorry, all of our representatives are busy right now. Please try again later. Goodbye.",
    "rate": "I'm sorry, we cannot take another call from this number right now. Please try again later. Goodbye.",
    "spend": "I'm sorry, this line is not taking calls right now. Please try again tomorrow. Goodbye.",
    "time": "I'm sorry, we have reached the time limit for this call. Goodbye.",
}


async def synthesize(config: Config, text: str) -> bytes:
    client = SonioxTTSClient(api_key=config.soniox_api_key, voice=config.tts_voice, model=config.tts_model, sample_rate=8000)
    await client.connect()
    try:
        synthesis_id = await client.synthesize(text)
        chunks = []
        async for event in client.events():
            if isinstance(event, TTSAudioChunk) and event.synthesis_id == synthesis_id:
                chunks.append(event.pcm16_bytes)
            elif isinstance(event, TTSSynthesisComplete) and event.synthesis_id == synthesis_id:
                break
            elif isinstance(event, TTSError):
                raise RuntimeError(event.message)
        return b"".join(chunks)
    finally:
        await client.close()


class Refusals:
    def __init__(self):
        self.audio: Dict[str, bytes] = {}

    async def prepare(self, config: Config) -> None:
        """Have the audio for every phrase, from the cache or by synthesizing it now. A phrase that fails is logged and left out; nothing here stops the line starting."""
        cache = Path(config.audio_cache_dir)
        for name, text in PHRASES.items():
            path = cache / (hashlib.sha256(f"{config.tts_voice}|{config.tts_model}|{text}".encode()).hexdigest()[:24] + ".pcm")
            try:
                if path.is_file():
                    self.audio[name] = path.read_bytes()
                    continue
                pcm = await synthesize(config, text)
                if not pcm:
                    raise RuntimeError("no audio came back")
                cache.mkdir(parents=True, exist_ok=True)
                path.write_bytes(pcm)
                self.audio[name] = pcm
            except Exception as exc:
                logger.warning("could not prepare the spoken refusal %r: %s", name, exc)
