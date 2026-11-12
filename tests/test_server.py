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

KEY = "test-answer-key-0123456789"
CONFIG = Config.from_env({"SONIOX_API_KEY": "k", "ANTHROPIC_API_KEY": "m", "FAKE_PAYER_NUMBER": "+12025550123", "ANSWER_KEY": KEY})


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


async def ws_url(port, call_uuid, caller="12025550100"):
    """What Vonage does: ask the answer request what to do, then connect where it says (here to the local port, not the public host)."""
    status, _, body = await http_get(port, f"/answer?key={KEY}&uuid={call_uuid}&from={caller}")
    assert status == 200
    return json.loads(body)[0]["endpoint"][0]["uri"].replace("wss://line.example.org", f"ws://127.0.0.1:{port}")


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
        status, headers, body = await http_get(port, f"/answer?key={KEY}&uuid=abc-123&from=12025550100&to=12025550123")
        assert status == 200 and headers["content-type"] == "application/json" and headers["_content_type_count"] == 1      # exactly one, and it is JSON
        [action] = json.loads(body)
        [endpoint] = action["endpoint"]
        assert action["action"] == "connect" and endpoint["type"] == "websocket" and endpoint["content-type"] == "audio/l16;rate=8000"
        assert endpoint["uri"].startswith("wss://line.example.org/ws/abc-123?") and "2025550100" not in endpoint["uri"]          # a signed token, and no phone number
    finally:
        await context.__aexit__(None, None, None)


@pytest.mark.parametrize("path", [f"/answer?key={KEY}", f"/answer?key={KEY}&uuid=", f"/answer?key={KEY}&uuid=../../etc", f"/answer?key={KEY}&uuid=" + "a" * 200])
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
        async with connect(await ws_url(port, "call-1")) as call:
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
        async with connect(await ws_url(port, "call-2")) as call:
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
        async with connect(await ws_url(port, "call-77")) as call:
            await until(lambda: "tts" in legs and legs["tts"].synthesized)
            await legs["tts"].queue.put(TTSSynthesisComplete(synthesis_id="greeting-1"))
            await asyncio.sleep(0.05)
            for event in (STTSpeechStarted(), STTFinalTranscript(text="hello"), STTSpeechEnded()):
                await legs["stt"].queue.put(event)
            await until(lambda: seen)
    finally:
        await context.__aexit__(None, None, None)
    assert seen["tools"] == ["lookup_coverage"] and call_reference("call-77") in seen["prompt"]


@pytest.mark.parametrize("path", ["/answer?uuid=abc-123", "/answer?key=wrong-key-wrong-key&uuid=abc-123", "/answer?key=&uuid=abc-123"])
async def test_an_answer_request_without_the_answer_key_is_refused(path):
    context, port = await running(FakePayerServer(CONFIG))
    try:
        assert (await http_get(port, path))[0] == 403
    finally:
        await context.__aexit__(None, None, None)


async def test_the_audio_websocket_cannot_be_opened_without_going_through_the_answer_request(legs):
    """Without this anyone could stream audio straight in and spend our speech and model credit with no phone call at all."""
    context, port = await running(FakePayerServer(CONFIG))
    try:
        good = await ws_url(port, "call-9")
        for url in (f"ws://127.0.0.1:{port}/ws/call-9",                       # no token
                    good.replace("call-9", "call-10"),                         # a token for another call
                    good.split("&t=")[0] + "&t=" + "0" * 32):                  # a forged one
            with pytest.raises(InvalidStatus) as refused:
                async with connect(url):
                    pass
            assert refused.value.response.status_code == 403, url
        assert "tts" not in legs                                               # no speech service was ever touched
    finally:
        await context.__aexit__(None, None, None)


def limited_config(**env):
    return Config.from_env({"SONIOX_API_KEY": "k", "ANTHROPIC_API_KEY": "m", "FAKE_PAYER_NUMBER": "+12025550123", "ANSWER_KEY": KEY, **env})


def refusal_audio():
    from fake_payer.refusals import Refusals
    refusals = Refusals()
    refusals.audio = {"busy": b"B" * 640, "rate": b"R" * 640, "spend": b"S" * 640, "time": b"T" * 640, "hold": b"H" * 640}
    return refusals


@pytest.fixture
def counted(monkeypatch):
    """Fake speech legs that count how many calls ever reached them, so a refused call can be shown to have used none."""
    made = {"stt": [], "tts": []}
    monkeypatch.setattr(bridge_module, "SonioxSTTClient", lambda **kw: made["stt"].append(FakeSTT(**kw)) or made["stt"][-1])
    monkeypatch.setattr(bridge_module, "SonioxTTSClient", lambda **kw: made["tts"].append(FakeTTS(**kw)) or made["tts"][-1])
    from fake_payer import audio
    monkeypatch.setattr(audio, "FRAME_SECONDS", 0)
    return made


async def refusal_heard(port, call_uuid, caller):
    async with connect(await ws_url(port, call_uuid, caller)) as call:
        received = bytearray()
        try:
            while True:
                received += await asyncio.wait_for(call.recv(), 2)
        except Exception:
            pass
        return bytes(received)


async def test_a_call_over_the_simultaneous_cap_hears_the_busy_message_and_uses_no_speech_service(counted):
    config = limited_config(MAX_CONCURRENT_CALLS="1")
    context, port = await running(FakePayerServer(config, refusals=refusal_audio()))
    try:
        async with connect(await ws_url(port, "first", "12025550100")):
            await until(lambda: counted["tts"])
            assert await refusal_heard(port, "second", "12025550101") == b"B" * 640
            assert len(counted["tts"]) == 1 and len(counted["stt"]) == 1                # only the first call ever reached speech services
    finally:
        await context.__aexit__(None, None, None)


async def test_a_number_over_its_hourly_count_is_refused_and_another_number_is_not(counted):
    config = limited_config(MAX_CALLS_PER_NUMBER_PER_HOUR="1")
    context, port = await running(FakePayerServer(config, refusals=refusal_audio()))
    try:
        async with connect(await ws_url(port, "a1", "12025550100")):
            await until(lambda: counted["tts"])
        await asyncio.sleep(0.1)                                                        # the first call has ended and released its place
        assert await refusal_heard(port, "a2", "12025550100") == b"R" * 640
        async with connect(await ws_url(port, "b1", "12025550199")):
            await until(lambda: len(counted["tts"]) == 2)
    finally:
        await context.__aexit__(None, None, None)


async def test_a_second_stream_for_a_call_already_running_is_not_allowed(counted):
    context, port = await running(FakePayerServer(limited_config(), refusals=refusal_audio()))
    try:
        url = await ws_url(port, "dup", "12025550100")
        async with connect(url):
            await until(lambda: counted["tts"])
            async with connect(url) as second:
                with pytest.raises(Exception):
                    await asyncio.wait_for(second.recv(), 2)                            # closed without anything said
            assert len(counted["tts"]) == 1
    finally:
        await context.__aexit__(None, None, None)


async def test_a_finished_call_leaves_one_log_entry_without_the_callers_number(counted, tmp_path, monkeypatch):
    from fake_payer.calllog import CallLog
    from fake_payer.persona import call_reference

    async def fake_model(messages, tools, system_prompt, on_text, on_tool_use, cancel_event, **_):
        await on_text("Hello.")
        return StreamedAssistantMessage(content=[{"type": "text", "text": "Hello. Eighty percent."}], stop_reason="end_turn")
    monkeypatch.setattr(bridge_module, "run_claude_turn", fake_model)
    config = limited_config(CALL_LOG_DIR=str(tmp_path / "log"))
    context, port = await running(FakePayerServer(config, refusals=refusal_audio()))
    try:
        async with connect(await ws_url(port, "logged-1", "12025550142")):
            await until(lambda: counted["tts"])
            await counted["tts"][0].queue.put(TTSSynthesisComplete(synthesis_id="greeting-1"))
            await asyncio.sleep(0.05)
            for event in (STTSpeechStarted(), STTFinalTranscript(text="hello"), STTSpeechEnded()):
                await counted["stt"][0].queue.put(event)
            await until(lambda: counted["tts"][0].finalized)                      # the reply was spoken
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)
    finally:
        await context.__aexit__(None, None, None)
    log = CallLog(str(tmp_path / "log"))
    record = log.get(call_reference("logged-1"))
    assert record is not None and record["caller"] == "***0142" and record["ended"] == "caller_hung_up"
    assert [t["role"] for t in record["turns"]][:2] == ["assistant", "user"] and record["turns"][0]["text"] == DISCLOSURE
    assert "2025550142" not in json.dumps(record) and len(list((tmp_path / "log").glob("*.json"))) == 1


async def test_a_refused_call_is_logged_with_the_reason(counted, tmp_path):
    from fake_payer.calllog import CallLog
    from fake_payer.persona import call_reference
    config = limited_config(MAX_CONCURRENT_CALLS="1", CALL_LOG_DIR=str(tmp_path / "log"))
    context, port = await running(FakePayerServer(config, refusals=refusal_audio()))
    try:
        async with connect(await ws_url(port, "first-1", "12025550100")):
            await until(lambda: counted["tts"])
            await refusal_heard(port, "second-1", "12025550101")
    finally:
        await context.__aexit__(None, None, None)
    record = CallLog(str(tmp_path / "log")).get(call_reference("second-1"))
    assert record["ended"] == "refused_busy" and record["caller"] == "***0101" and record["turns"] == []
