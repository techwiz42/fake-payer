"""The voice bridge, driven by fake speech services, a fake model turn and a fake call: no network. Covers the greeting, a turn, a tool, barge-in and the call-length watchdog."""
import asyncio

import pytest

from fake_payer import bridge as bridge_module
from fake_payer.anthropic_streaming_client import StreamedAssistantMessage
from fake_payer.bridge import BridgeSettings, VoiceBridge
from fake_payer.soniox_streaming_client import (
    STTFinalTranscript, STTSpeechEnded, STTSpeechStarted, TTSAudioChunk, TTSSynthesisComplete,
)

END = object()


class FakeSTT:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.audio = []
        self.queue = asyncio.Queue()

    async def connect(self): pass
    async def send_audio(self, data): self.audio.append(data)
    async def finalize(self): pass
    async def close(self): self.queue.put_nowait(END)

    async def events(self):
        while True:
            event = await self.queue.get()
            if event is END:
                return
            yield event


class FakeTTS:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.queue = asyncio.Queue()
        self.synthesized, self.chunks, self.finalized, self.cancelled, self.streams = [], [], [], 0, 0

    async def connect(self): pass
    async def close(self): self.queue.put_nowait(END)
    async def synthesize(self, text, language=None):
        self.synthesized.append(text)
        return f"greeting-{len(self.synthesized)}"

    async def begin_stream(self, language=None):
        self.streams += 1
        return f"stream-{self.streams}"

    async def send_text_chunk(self, text, synthesis_id): self.chunks.append((synthesis_id, text))
    async def finalize_stream(self, synthesis_id): self.finalized.append(synthesis_id)
    async def cancel(self): self.cancelled += 1

    async def events(self):
        while True:
            event = await self.queue.get()
            if event is END:
                return
            yield event


class FakeCall:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.played, self.closed = [], asyncio.Event()

    async def receive_audio(self):
        data = await self.incoming.get()
        return None if data is END else data

    async def send_audio(self, data): self.played.append(data)
    async def close(self):
        self.closed.set()
        self.incoming.put_nowait(END)


@pytest.fixture
def env(monkeypatch):
    made = {}

    def stt_factory(**kw): made["stt"] = FakeSTT(**kw); return made["stt"]
    def tts_factory(**kw): made["tts"] = FakeTTS(**kw); return made["tts"]
    monkeypatch.setattr(bridge_module, "SonioxSTTClient", stt_factory)
    monkeypatch.setattr(bridge_module, "SonioxTTSClient", tts_factory)
    made["call"] = FakeCall()
    made["turns"] = []                  # what the fake model was asked: a snapshot of the messages each time
    return made


def settings(**kw):
    return BridgeSettings(call_uuid="call-1", greeting="This is a fictional line.", soniox_api_key="key", **kw)


def install_model(monkeypatch, env, script):
    """`script(messages, on_text, cancel_event)` is an async function returning a StreamedAssistantMessage or None."""
    async def fake_run_claude_turn(messages, tools, system_prompt, on_text, on_tool_use, cancel_event, **_):
        env["turns"].append([dict(m) for m in messages])
        return await script(messages, on_text, cancel_event)
    monkeypatch.setattr(bridge_module, "run_claude_turn", fake_run_claude_turn)


async def until(predicate, seconds=2.0):
    for _ in range(int(seconds / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


def text_message(text):
    return StreamedAssistantMessage(content=[{"type": "text", "text": text}], stop_reason="end_turn")


async def say(env, text):
    """The caller says something: Soniox sees speech start, a final transcript, then the speech end."""
    for event in (STTSpeechStarted(), STTFinalTranscript(text=text), STTSpeechEnded()):
        await env["stt"].queue.put(event)


async def test_the_greeting_is_spoken_first_in_the_configured_voice_and_the_model_knows_it(env, monkeypatch):
    bridge = VoiceBridge(settings(tts_voice="Mark"), {}, "persona")
    task = asyncio.create_task(bridge.run(env["call"]))
    await until(lambda: "tts" in env and env["tts"].synthesized)
    assert env["tts"].synthesized == ["This is a fictional line."]
    assert env["tts"].kwargs["voice"] == "Mark" and env["tts"].kwargs["sample_rate"] == 8000
    assert env["stt"].kwargs["sample_rate"] == 8000
    env["call"].incoming.put_nowait(END)
    await task
    assert bridge.terminated_by == "caller"
    assert bridge.transcript[0].role == "assistant" and bridge.transcript[0].text == "This is a fictional line."
    assert bridge._messages[1] == {"role": "assistant", "content": "This is a fictional line."}


async def test_caller_audio_goes_to_speech_to_text_untouched(env, monkeypatch):
    bridge = VoiceBridge(settings(), {}, "persona")
    task = asyncio.create_task(bridge.run(env["call"]))
    await until(lambda: "stt" in env)
    env["call"].incoming.put_nowait(b"\x01\x02" * 80)
    await until(lambda: env["stt"].audio)
    assert env["stt"].audio == [b"\x01\x02" * 80]
    env["call"].incoming.put_nowait(END)
    await task


async def test_a_turn_streams_the_models_words_into_speech_and_plays_the_audio(env, monkeypatch):
    async def script(messages, on_text, cancel):
        await on_text("Your deductible ")
        await on_text("is fifty dollars.")
        return text_message("Your deductible is fifty dollars.")
    install_model(monkeypatch, env, script)
    bridge = VoiceBridge(settings(), {}, "persona")
    task = asyncio.create_task(bridge.run(env["call"]))
    await until(lambda: "stt" in env and env["tts"].synthesized)
    await say(env, "what is my deductible")
    await until(lambda: env["tts"].finalized)
    assert env["tts"].chunks == [("stream-1", "Your deductible "), ("stream-1", "is fifty dollars.")]
    assert env["turns"][0][-1] == {"role": "user", "content": "what is my deductible"}
    await env["tts"].queue.put(TTSAudioChunk(synthesis_id="stream-1", pcm16_bytes=b"audio-1"))
    await env["tts"].queue.put(TTSSynthesisComplete(synthesis_id="stream-1"))
    await until(lambda: env["call"].played)
    assert env["call"].played == [b"audio-1"]
    env["call"].incoming.put_nowait(END)
    await task
    assert [t.role for t in bridge.transcript] == ["assistant", "user", "assistant"]
    assert bridge.transcript[-1].text == "Your deductible is fifty dollars."


async def test_a_tool_the_model_asks_for_is_run_and_its_result_goes_back_to_the_model(env, monkeypatch):
    calls = []

    async def lookup(arguments):
        calls.append(arguments)
        return {"percent": 80}

    async def script(messages, on_text, cancel):
        if len(env["turns"]) == 1:
            return StreamedAssistantMessage(content=[{"type": "tool_use", "id": "t1", "name": "lookup", "input": {"code": "D0120"}}], stop_reason="tool_use")
        await on_text("Eighty percent.")
        return text_message("Eighty percent.")
    install_model(monkeypatch, env, script)
    bridge = VoiceBridge(settings(), {"lookup": ({"name": "lookup"}, lookup)}, "persona")
    task = asyncio.create_task(bridge.run(env["call"]))
    await until(lambda: "stt" in env and env["tts"].synthesized)
    await say(env, "what does the exam pay")
    await until(lambda: env["tts"].finalized)
    assert calls == [{"code": "D0120"}]
    result_block = env["turns"][1][-1]["content"][0]
    assert result_block["type"] == "tool_result" and result_block["tool_use_id"] == "t1" and '"percent": 80' in result_block["content"]
    env["call"].incoming.put_nowait(END)
    await task


async def test_an_unknown_or_failing_tool_is_reported_to_the_model_and_the_call_goes_on(env, monkeypatch):
    async def broken(arguments):
        raise RuntimeError("boom with a secret detail")

    async def script(messages, on_text, cancel):
        if len(env["turns"]) == 1:
            return StreamedAssistantMessage(content=[
                {"type": "tool_use", "id": "a", "name": "missing", "input": {}},
                {"type": "tool_use", "id": "b", "name": "broken", "input": {}}], stop_reason="tool_use")
        await on_text("I could not look that up.")
        return text_message("I could not look that up.")
    install_model(monkeypatch, env, script)
    bridge = VoiceBridge(settings(), {"broken": ({"name": "broken"}, broken)}, "persona")
    task = asyncio.create_task(bridge.run(env["call"]))
    await until(lambda: "stt" in env and env["tts"].synthesized)
    await say(env, "anything")
    await until(lambda: env["tts"].finalized)
    blocks = env["turns"][1][-1]["content"]
    assert all(b["is_error"] for b in blocks) and "secret detail" not in str(blocks)
    assert bridge.terminated_by is None
    env["call"].incoming.put_nowait(END)
    await task


async def test_barge_in_stops_speech_and_the_model_and_drops_audio_already_buffered(env, monkeypatch):
    seen_cancel = {}

    async def script(messages, on_text, cancel):
        seen_cancel["event"] = cancel
        await on_text("Let me read you the whole plan. ")
        await cancel.wait()                    # still talking when the caller breaks in
        return text_message("never finished")
    install_model(monkeypatch, env, script)
    bridge = VoiceBridge(settings(), {}, "persona")
    task = asyncio.create_task(bridge.run(env["call"]))
    await until(lambda: "stt" in env and env["tts"].synthesized)
    await env["tts"].queue.put(TTSSynthesisComplete(synthesis_id="greeting-1"))      # the greeting has finished playing
    await asyncio.sleep(0.05)
    assert env["tts"].cancelled == 0
    await say(env, "tell me about the plan")
    await until(lambda: env["tts"].chunks)
    await env["tts"].queue.put(TTSAudioChunk(synthesis_id="stream-1", pcm16_bytes=b"before"))
    await until(lambda: env["call"].played)
    await env["stt"].queue.put(STTSpeechStarted())               # the caller talks over the line
    await until(lambda: env["tts"].cancelled)
    assert seen_cancel["event"].is_set()
    await env["tts"].queue.put(TTSAudioChunk(synthesis_id="stream-1", pcm16_bytes=b"stale"))
    await asyncio.sleep(0.05)
    assert env["call"].played == [b"before"]                     # the buffered audio of the cancelled reply never plays
    assert not env["tts"].finalized                              # and a cancelled stream is not also finalized
    assert [t.role for t in bridge.transcript] == ["assistant", "user"]      # a cancelled reply is not kept
    env["call"].incoming.put_nowait(END)
    await task


async def test_the_watchdog_hangs_up_at_the_maximum_call_length(env, monkeypatch):
    bridge = VoiceBridge(settings(max_call_seconds=0.2), {}, "persona")
    await asyncio.wait_for(bridge.run(env["call"]), timeout=3)
    assert env["call"].closed.is_set() and bridge.terminated_by == "watchdog"


async def test_a_missing_soniox_key_stops_the_call_before_it_starts(env):
    bridge = VoiceBridge(BridgeSettings(call_uuid="c", greeting="hi", soniox_api_key=""), {}, "persona")
    with pytest.raises(RuntimeError):
        await bridge.run(env["call"])
    assert bridge.terminated_by == "error"


async def test_the_caller_talking_over_the_greeting_cuts_it_off(env, monkeypatch):
    bridge = VoiceBridge(settings(), {}, "persona")
    task = asyncio.create_task(bridge.run(env["call"]))
    await until(lambda: "stt" in env and env["tts"].synthesized)
    await env["stt"].queue.put(STTSpeechStarted())
    await until(lambda: env["tts"].cancelled == 1)
    env["call"].incoming.put_nowait(END)
    await task
