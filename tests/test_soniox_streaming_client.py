# Copied from agent_framework b227c23b: core/tests/test_soniox_streaming_client.py
# Changes: import path.
# Changes from the original are listed in this header; everything else is as it was.
"""
core/soniox_streaming_client.py tests -- multi-language support, added
2026-09-30 for the public booking chat's "recognize non-English languages
and respond in kind" requirement (shared by the phone bridge's own STT/TTS
usage, which stays single-language via the same defaults it always had).

Covers the two real, previously-untested pieces: SonioxSTTClient's config
frame (language_hints / enable_language_identification) and _parse_event's
per-token language extraction, plus SonioxTTSClient's per-call language
override. Connection-level tests mock `websockets.connect`; _parse_event
tests call it directly against a constructed client -- it's a pure function
of the raw message, no live connection needed.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from fake_payer.soniox_streaming_client import (
    SonioxSTTClient,
    SonioxTTSClient,
    STTFinalTranscript,
    STTPartialTranscript,
)

MODULE = "fake_payer.soniox_streaming_client"


class _FakeWSConnection:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send(self, payload):
        self.sent.append(payload)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_stt_default_config_is_single_language_no_identification():
    """Backward compatibility: the phone bridge constructs this client with
    only `language` -- must get the exact same config shape it always did."""
    fake_ws = _FakeWSConnection()
    with patch(f"{MODULE}.websockets.connect", new=AsyncMock(return_value=fake_ws)):
        client = SonioxSTTClient(api_key="key", model="stt-rt-preview", language="en", sample_rate=8000)
        await client.connect()

    sent_config = json.loads(fake_ws.sent[0])
    assert sent_config["language_hints"] == ["en"]
    assert sent_config["enable_language_identification"] is False


@pytest.mark.asyncio
async def test_stt_custom_language_hints_and_identification_enabled():
    fake_ws = _FakeWSConnection()
    with patch(f"{MODULE}.websockets.connect", new=AsyncMock(return_value=fake_ws)):
        client = SonioxSTTClient(
            api_key="key", model="stt-rt-preview", sample_rate=16000,
            language_hints=["en", "es", "zh"], enable_language_identification=True,
        )
        await client.connect()

    sent_config = json.loads(fake_ws.sent[0])
    assert sent_config["language_hints"] == ["en", "es", "zh"]
    assert sent_config["enable_language_identification"] is True


def test_parse_event_extracts_final_transcript_language_from_tokens():
    client = SonioxSTTClient(api_key="key", model="stt-rt-preview")
    raw = json.dumps({"tokens": [
        {"text": "Hola", "is_final": True, "language": "es"},
        {"text": " doctor", "is_final": True, "language": "es"},
    ]})
    event = client._parse_event(raw)
    assert isinstance(event, STTFinalTranscript)
    assert event.text == "Hola doctor"
    assert event.language == "es"


def test_parse_event_extracts_partial_transcript_language():
    client = SonioxSTTClient(api_key="key", model="stt-rt-preview")
    raw = json.dumps({"tokens": [{"text": "Bonjour", "is_final": False, "language": "fr"}]})
    event = client._parse_event(raw)
    assert isinstance(event, STTPartialTranscript)
    assert event.language == "fr"


def test_parse_event_language_defaults_to_none_when_absent():
    """Real Soniox responses that don't tag a language (e.g. identification
    disabled) must not crash or fabricate a language -- existing single-
    language behavior, preserved exactly."""
    client = SonioxSTTClient(api_key="key", model="stt-rt-preview")
    raw = json.dumps({"tokens": [{"text": "hello there", "is_final": True}]})
    event = client._parse_event(raw)
    assert isinstance(event, STTFinalTranscript)
    assert event.text == "hello there"
    assert event.language is None


@pytest.mark.asyncio
async def test_tts_begin_stream_uses_constructor_language_by_default():
    fake_ws = _FakeWSConnection()
    with patch(f"{MODULE}.websockets.connect", new=AsyncMock(return_value=fake_ws)):
        client = SonioxTTSClient(api_key="key", voice="Adrian", model="tts-rt-v1", language="en")
        await client.connect()
        await client.begin_stream()

    sent_config = json.loads(fake_ws.sent[0])
    assert sent_config["language"] == "en"


@pytest.mark.asyncio
async def test_tts_begin_stream_language_override():
    fake_ws = _FakeWSConnection()
    with patch(f"{MODULE}.websockets.connect", new=AsyncMock(return_value=fake_ws)):
        client = SonioxTTSClient(api_key="key", voice="Adrian", model="tts-rt-v1", language="en")
        await client.connect()
        await client.begin_stream(language="es")

    sent_config = json.loads(fake_ws.sent[0])
    assert sent_config["language"] == "es"


@pytest.mark.asyncio
async def test_tts_synthesize_passes_through_language_override():
    fake_ws = _FakeWSConnection()
    with patch(f"{MODULE}.websockets.connect", new=AsyncMock(return_value=fake_ws)):
        client = SonioxTTSClient(api_key="key", voice="Adrian", model="tts-rt-v1", language="en")
        await client.connect()
        await client.synthesize("Hola, ¿en qué puedo ayudar?", language="es")

    sent_config = json.loads(fake_ws.sent[0])
    assert sent_config["language"] == "es"


# ---- authentication: the key rides on the connection (Authorization header), never in a message ----------------------------------------
# Soniox refuses a first-message key after 2027-01-15, and answers 400 to a header AND a first-message key together.
import ast
import logging
from pathlib import Path

from fake_payer.soniox_streaming_client import STTError, TTSError, _auth_headers, _header_kwargs


def _sent_headers(connect_mock) -> dict:
    """The upgrade headers handed to websockets.connect, under whichever keyword this library version uses."""
    kwargs = connect_mock.call_args.kwargs
    return kwargs.get("additional_headers") or kwargs.get("extra_headers")


@pytest.mark.parametrize("version,keyword", [("12.0", "extra_headers"), ("13.1", "extra_headers"), ("14.0", "additional_headers"), ("16.0", "additional_headers")])
def test_the_header_keyword_follows_the_websockets_version(version, keyword):
    """Generated apps pin websockets>=12,<13 (extra_headers); the repo's own environment is 16 (additional_headers). The wrong keyword is a
    TypeError on every connect: it silenced all speech on 2026-10-09."""
    with patch(f"{MODULE}.websockets.__version__", version):
        assert list(_header_kwargs({"Authorization": "Bearer k"})) == [keyword]


@pytest.mark.asyncio
async def test_a_real_socket_server_receives_the_authorization_header_from_both_clients(monkeypatch):
    """No mock of the library: the installed websockets opens a real connection to a local server, which reports the header it received."""
    import asyncio
    import websockets

    seen = []

    async def handler(ws, *_path):
        request = getattr(ws, "request", None)
        headers = request.headers if request is not None else ws.request_headers
        seen.append(headers.get("Authorization"))
        try:
            async for _ in ws:      # stay open and read, as the real service does, until the test closes the server
                pass
        except Exception:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(f"{MODULE}.SONIOX_STT_URL", f"ws://127.0.0.1:{port}/stt")
    monkeypatch.setattr(f"{MODULE}.SONIOX_TTS_URL", f"ws://127.0.0.1:{port}/tts")
    try:
        stt = SonioxSTTClient(api_key="live-key-stt", model="stt-rt-preview")
        await stt.connect()
        tts = SonioxTTSClient(api_key="live-key-tts", voice="Adrian", model="tts-rt-v1")
        await tts.connect()
        await asyncio.sleep(0.2)
    finally:
        server.close()
        await server.wait_closed()
    assert sorted(seen) == ["Bearer live-key-stt", "Bearer live-key-tts"]


@pytest.mark.asyncio
async def test_stt_authenticates_with_a_bearer_header_and_sends_no_key_in_the_start_message():
    fake_ws = _FakeWSConnection()
    connect = AsyncMock(return_value=fake_ws)
    with patch(f"{MODULE}.websockets.connect", new=connect):
        client = SonioxSTTClient(api_key="secret-key-1", model="stt-rt-preview", language="en", sample_rate=8000)
        await client.connect()

    assert _sent_headers(connect) == {"Authorization": "Bearer secret-key-1"}
    assert "api_key" not in json.loads(fake_ws.sent[0])
    assert "secret-key-1" not in fake_ws.sent[0]


@pytest.mark.asyncio
async def test_tts_authenticates_with_a_bearer_header_and_sends_no_key_in_any_stream_config():
    fake_ws = _FakeWSConnection()
    connect = AsyncMock(return_value=fake_ws)
    with patch(f"{MODULE}.websockets.connect", new=connect):
        client = SonioxTTSClient(api_key="secret-key-2", voice="Adrian", model="tts-rt-v1", language="en")
        await client.connect()
        await client.begin_stream()
        await client.begin_stream(language="es")

    assert _sent_headers(connect) == {"Authorization": "Bearer secret-key-2"}
    for frame in fake_ws.sent:
        assert "api_key" not in json.loads(frame)
        assert "secret-key-2" not in frame


@pytest.mark.parametrize("key", [None, "", "   "])
def test_a_missing_key_fails_loudly_before_any_connection_is_made(key):
    with pytest.raises(ValueError, match="SONIOX_API_KEY"):
        _auth_headers(key)


def test_a_blank_key_is_refused_when_the_client_is_built_so_no_socket_can_open():
    """The constructors already refuse a blank key; `_auth_headers` is the second line of defence at connect time."""
    for build in (lambda: SonioxSTTClient(api_key="", model="stt-rt-preview"),
                  lambda: SonioxTTSClient(api_key=None, voice="Adrian", model="tts-rt-v1")):
        with pytest.raises((ValueError, TypeError), match="api_key"):
            build()


@pytest.mark.asyncio
async def test_the_key_is_never_written_to_the_log(caplog):
    fake_ws = _FakeWSConnection()
    with caplog.at_level(logging.DEBUG), patch(f"{MODULE}.websockets.connect", new=AsyncMock(return_value=fake_ws)):
        await SonioxSTTClient(api_key="secret-key-3", model="stt-rt-preview").connect()
        tts = SonioxTTSClient(api_key="secret-key-3", voice="Adrian", model="tts-rt-v1")
        await tts.connect()
    assert "secret-key-3" not in caplog.text


def test_stt_reads_soniox_flat_error_frame_as_a_fatal_error_not_as_silence():
    """The frame Soniox really sends for a rejected key (captured 2026-10-09) has empty tokens beside the error fields."""
    client = SonioxSTTClient(api_key="k", model="stt-rt-preview")
    raw = json.dumps({"tokens": [], "final_audio_proc_ms": 0, "total_audio_proc_ms": 0, "error_code": 401,
                      "error_type": "unauthenticated", "error_message": "Incorrect API key provided."})
    events = client._parse_event(raw)
    events = events if isinstance(events, list) else [events]
    errors = [e for e in events if isinstance(e, STTError)]
    assert len(errors) == 1 and errors[0].fatal is True and errors[0].code == "401"
    assert "unauthenticated" in errors[0].message and "Incorrect API key" in errors[0].message


@pytest.mark.parametrize("code,fatal", [(401, True), (403, True), (429, True), (500, True), (400, False)])
def test_tts_marks_a_rejected_key_and_a_full_connection_limit_as_fatal(code, fatal):
    client = SonioxTTSClient(api_key="k", voice="Adrian", model="tts-rt-v1")
    raw = json.dumps({"stream_id": "", "error_code": code, "error_message": "x", "error_type": "t"})
    event = client._parse_event(raw)
    assert isinstance(event, TTSError) and event.fatal is fatal


def test_no_soniox_start_message_anywhere_in_the_code_carries_an_api_key_field():
    """Guards against the old first-message form coming back: a dict with both "api_key" and a Soniox "model" key is a start message."""
    root = Path(__file__).resolve().parents[2]
    offenders = []
    for base in ("core", "modules"):
        for path in (root / base).rglob("*.py"):
            if "/tests/" in str(path) or "/migrations/" in str(path):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                    if "api_key" in keys and ("audio_format" in keys or "stream_id" in keys):
                        offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert offenders == []


def test_parse_event_keeps_where_the_spaces_fall_in_raw_text():
    """Soniox tokens carry their own leading space; `text` is stripped, `raw_text` is not."""
    client = SonioxSTTClient(api_key="key", model="stt-rt-preview")
    raw = json.dumps({"tokens": [{"text": " 19", "is_final": True}, {"text": "69", "is_final": True}]})
    event = client._parse_event(raw)
    assert event.text == "1969"
    assert event.raw_text == " 1969"
    assert event.segment == " 1969"


def test_a_piece_that_continues_the_previous_one_has_no_leading_space():
    client = SonioxSTTClient(api_key="key", model="stt-rt-preview")
    event = client._parse_event(json.dumps({"tokens": [{"text": ".", "is_final": True}]}))
    assert (event.text, event.raw_text, event.segment) == (".", ".", ".")


def test_a_partial_transcript_keeps_its_raw_text_too():
    client = SonioxSTTClient(api_key="key", model="stt-rt-preview")
    event = client._parse_event(json.dumps({"tokens": [{"text": " appointment", "is_final": False}]}))
    assert isinstance(event, STTPartialTranscript)
    assert (event.text, event.segment) == ("appointment", " appointment")


def test_an_event_built_without_raw_text_is_still_a_separate_word():
    assert STTFinalTranscript(text="hello").segment == " hello"
    assert STTPartialTranscript(text="world").segment == " world"
