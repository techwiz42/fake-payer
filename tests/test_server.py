"""The server: the answer request, the audio websocket, and nothing else. Real HTTP and websocket connections to a server on a free port; only the Soniox and model legs are fakes."""
import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from fake_payer import bridge as bridge_module
from fake_payer.anthropic_streaming_client import StreamedAssistantMessage
from fake_payer.config import Config
from fake_payer.persona import DISCLOSURE
from fake_payer.server import FakePayerServer
from fake_payer.soniox_streaming_client import STTFinalTranscript, STTSpeechEnded, STTSpeechStarted, TTSAudioChunk, TTSSynthesisComplete
from tests.test_bridge import FakeSTT, FakeTTS, until

CONFIG = Config.from_env({"SONIOX_API_KEY": "k", "ANTHROPIC_API_KEY": "m", "FAKE_PAYER_NUMBER": "+12025550123"})


async def http_get(port, path, host="line.example.org"):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
    await writer.drain()
    raw = await reader.read()
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    pairs = [tuple(line.split(": ", 1)) for line in lines[1:]]
    headers = {k.lower(): v for k, v in pairs}
    headers["_content_type_count"] = sum(1 for k, _ in pairs if k.lower() == "content-type")
    return int(lines[0].split()[1]), headers, body.decode()


@pytest.fixture
def legs(monkeypatch):
    made = {}
    monkeypatch.setattr(bridge_module, "SonioxSTTClient", lambda **kw: made.setdefault("stt", FakeSTT(**kw)))
    monkeypatch.setattr(bridge_module, "SonioxTTSClient", lambda **kw: made.setdefault("tts", FakeTTS(**kw)))
    return made


async def running(server):
    context = server.serve("127.0.0.1", 0)
    running_server = await context.__aenter__()
    return context, running_server.sockets[0].getsockname()[1]


async def test_the_answer_request_returns_the_ncco_that_streams_audio_to_our_websocket():
    server = FakePayerServer(CONFIG)
    context, port = await running(server)
    try:
        status, headers, body = await http_get(port, "/answer?uuid=abc-123&from=12025550100&to=12025550123")
        assert status == 200 and headers["content-type"] == "application/json" and headers["_content_type_count"] == 1      # exactly one, and it is JSON
        assert json.loads(body) == [{"action": "connect", "endpoint": [{"type": "websocket", "uri": "wss://line.example.org/ws/abc-123", "content-type": "audio/l16;rate=8000"}]}]
    finally:
        await context.__aexit__(None, None, None)


@pytest.mark.parametrize("path", ["/answer", "/answer?uuid=", "/answer?uuid=../../etc", "/answer?uuid=" + "a" * 200])
async def test_an_answer_request_without_a_usable_call_id_is_refused(path):
    context, port = await running(FakePayerServer(CONFIG))
    try:
        assert (await http_get(port, path))[0] == 400
    finally:
        await context.__aexit__(None, None, None)


@pytest.mark.parametrize("path", ["/", "/index.html", "/health", "/answer/extra", "/ws", "/ws/", "/admin", "/calls"])
async def test_every_other_path_is_refused(path):
    context, port = await running(FakePayerServer(CONFIG))
    try:
        assert (await http_get(port, path))[0] == 404
    finally:
        await context.__aexit__(None, None, None)


async def test_a_websocket_on_any_other_path_is_refused():
    context, port = await running(FakePayerServer(CONFIG))
    try:
        with pytest.raises(InvalidStatus) as refused:
            async with connect(f"ws://127.0.0.1:{port}/other"):
                pass
        assert refused.value.response.status_code == 404
    finally:
        await context.__aexit__(None, None, None)


async def test_a_call_gets_the_disclosure_and_audio_flows_both_ways(legs):
    context, port = await running(FakePayerServer(CONFIG))
    try:
        async with connect(f"ws://127.0.0.1:{port}/ws/call-1") as call:
            await until(lambda: "tts" in legs and legs["tts"].synthesized)
            assert legs["tts"].synthesized == [DISCLOSURE]
            await call.send(json.dumps({"event": "websocket:connected"}))      # a control frame: no audio
            await call.send(b"\x00\x01" * 160)                                  # the caller's audio
            await until(lambda: legs["stt"].audio)
            assert legs["stt"].audio == [b"\x00\x01" * 160]
            await legs["tts"].queue.put(TTSAudioChunk(synthesis_id="greeting-1", pcm16_bytes=b"hello-audio"))
            assert await asyncio.wait_for(call.recv(), 2) == b"hello-audio"
    finally:
        await context.__aexit__(None, None, None)


async def test_a_whole_call_a_coverage_question_a_tool_and_a_spoken_answer(legs, monkeypatch):
    """3.2: the caller asks, the model asks the tool, the tool answers, the answer is spoken and arrives as audio on the call."""
    asked = []

    async def lookup(arguments):
        asked.append(arguments)
        return {"percent": 80}

    turns = []

    async def fake_model(messages, tools, system_prompt, on_text, on_tool_use, cancel_event, **_):
        turns.append(len(messages))
        if len(turns) == 1:
            return StreamedAssistantMessage(content=[{"type": "tool_use", "id": "t1", "name": "lookup", "input": {"code": "D0120"}}], stop_reason="tool_use")
        await on_text("The exam is covered at eighty percent.")
        return StreamedAssistantMessage(content=[{"type": "text", "text": "The exam is covered at eighty percent."}], stop_reason="end_turn")
    monkeypatch.setattr(bridge_module, "run_claude_turn", fake_model)

    server = FakePayerServer(CONFIG, tools={"lookup": ({"name": "lookup"}, lookup)}, system_prompt="persona")
    context, port = await running(server)
    try:
        async with connect(f"ws://127.0.0.1:{port}/ws/call-2") as call:
            await until(lambda: "tts" in legs and legs["tts"].synthesized)
            await legs["tts"].queue.put(TTSSynthesisComplete(synthesis_id="greeting-1"))
            await asyncio.sleep(0.05)
            for event in (STTSpeechStarted(), STTFinalTranscript(text="what does the exam pay"), STTSpeechEnded()):
                await legs["stt"].queue.put(event)
            await until(lambda: legs["tts"].finalized)
            assert asked == [{"code": "D0120"}]
            assert legs["tts"].chunks == [("stream-1", "The exam is covered at eighty percent.")]
            await legs["tts"].queue.put(TTSAudioChunk(synthesis_id="stream-1", pcm16_bytes=b"answer-audio"))
            assert await asyncio.wait_for(call.recv(), 2) == b"answer-audio"
    finally:
        await context.__aexit__(None, None, None)


async def test_by_default_a_call_runs_with_the_lookup_tool_and_the_persona_carrying_its_own_reference(legs, monkeypatch):
    from fake_payer.persona import call_reference
    seen = {}

    async def fake_model(messages, tools, system_prompt, on_text, on_tool_use, cancel_event, **_):
        seen.update(tools=[t["name"] for t in tools], prompt=system_prompt)
        return StreamedAssistantMessage(content=[{"type": "text", "text": "ok"}], stop_reason="end_turn")
    monkeypatch.setattr(bridge_module, "run_claude_turn", fake_model)

    context, port = await running(FakePayerServer(CONFIG))
    try:
        async with connect(f"ws://127.0.0.1:{port}/ws/call-77") as call:
            await until(lambda: "tts" in legs and legs["tts"].synthesized)
            await legs["tts"].queue.put(TTSSynthesisComplete(synthesis_id="greeting-1"))
            await asyncio.sleep(0.05)
            for event in (STTSpeechStarted(), STTFinalTranscript(text="hello"), STTSpeechEnded()):
                await legs["stt"].queue.put(event)
            await until(lambda: seen)
    finally:
        await context.__aexit__(None, None, None)
    assert seen["tools"] == ["lookup_coverage"] and call_reference("call-77") in seen["prompt"]
