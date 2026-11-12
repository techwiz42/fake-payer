"""The spoken refusals: made once in the configured voice, kept, and played from storage."""
import asyncio

from fake_payer import audio, refusals as refusals_module
from fake_payer.config import Config
from fake_payer.refusals import PHRASES, Refusals
from fake_payer.soniox_streaming_client import TTSAudioChunk, TTSSynthesisComplete


class SynthTTS:
    made = []

    def __init__(self, **kw):
        self.kw = kw
        self.said = []
        self.queue = asyncio.Queue()
        SynthTTS.made.append(self)

    async def connect(self): pass
    async def close(self): pass

    async def synthesize(self, text, language=None):
        self.said.append(text)
        await self.queue.put(TTSAudioChunk(synthesis_id="s", pcm16_bytes=text.encode()[:8]))
        await self.queue.put(TTSSynthesisComplete(synthesis_id="s"))
        return "s"

    async def events(self):
        while True:
            yield await self.queue.get()


def config(tmp_path, **env):
    return Config.from_env({"SONIOX_API_KEY": "k", "ANTHROPIC_API_KEY": "m", "FAKE_PAYER_NUMBER": "+12025550123", "ANSWER_KEY": "test-answer-key-0123456789",
                            "AUDIO_CACHE_DIR": str(tmp_path), **env})


async def test_every_phrase_is_synthesized_once_in_the_configured_voice(tmp_path, monkeypatch):
    SynthTTS.made = []
    monkeypatch.setattr(refusals_module, "SonioxTTSClient", SynthTTS)
    refusals = Refusals()
    await refusals.prepare(config(tmp_path))
    assert set(refusals.audio) == set(PHRASES) == {"busy", "rate", "spend", "time"}
    assert {c.kw["voice"] for c in SynthTTS.made} == {"Adrian"} and {c.kw["sample_rate"] for c in SynthTTS.made} == {8000}
    assert sorted(t for c in SynthTTS.made for t in c.said) == sorted(PHRASES.values())


async def test_the_second_start_uses_the_stored_audio_and_the_voice_is_part_of_what_is_stored(tmp_path, monkeypatch):
    SynthTTS.made = []
    monkeypatch.setattr(refusals_module, "SonioxTTSClient", SynthTTS)
    await Refusals().prepare(config(tmp_path))
    made_first = len(SynthTTS.made)
    again = Refusals()
    await again.prepare(config(tmp_path))
    assert len(SynthTTS.made) == made_first and set(again.audio) == set(PHRASES)          # nothing was synthesized the second time
    await Refusals().prepare(config(tmp_path, TTS_VOICE="Other"))
    assert len(SynthTTS.made) == made_first + len(PHRASES)                                  # a different voice is not served from Adrian's audio


async def test_a_phrase_that_cannot_be_made_is_left_out_and_the_line_can_still_start(tmp_path, monkeypatch):
    class Broken(SynthTTS):
        async def connect(self): raise RuntimeError("no network")
    monkeypatch.setattr(refusals_module, "SonioxTTSClient", Broken)
    refusals = Refusals()
    await refusals.prepare(config(tmp_path))
    assert refusals.audio == {}


async def test_audio_is_played_in_twenty_millisecond_frames(monkeypatch):
    monkeypatch.setattr(audio, "FRAME_SECONDS", 0)
    sent = []

    class Call:
        async def send_audio(self, data): sent.append(data)
    await audio.play_pcm(Call(), b"x" * 700)
    assert [len(f) for f in sent] == [320, 320, 60]
