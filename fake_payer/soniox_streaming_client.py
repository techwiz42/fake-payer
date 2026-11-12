# Copied from agent_framework b227c23b: core/soniox_streaming_client.py
# Changes: none.
# Changes from the original are listed in this header; everything else is as it was.
"""
Soniox real-time STT + TTS WebSocket clients.

Moved here 2026-09-30 (add-voice-streaming-booking-chat) from
modules/communication/backend/soniox_stt_client.py /
soniox_tts_client.py -- these were already generic Soniox WebSocket
protocol clients with no phone-call-specific logic at all (no Vonage
dependency, no PCM8k hardcoding -- sample_rate is a plain constructor
argument, already used at 8000 by the phone bridge only because that's
what Vonage provides natively). Only their *caller*
(soniox_stitched_bridge.py) is phone-specific. clinical_scheduling's
voice-enabled booking chat needed the exact same real-time primitives to
fix a real, measured production latency bug (a one-shot REST
transcribe-then-full-clip-TTS round trip took 20+ seconds; phone calls
using these same streaming clients start speaking within a fraction of a
second because audio is forwarded chunk-by-chunk instead of waited-for-in-
full) -- reusing this file across modules would have meant a forbidden
cross-module backend import, so it moves to core/ instead, exactly the
same fix as core/soniox_rest_client.py::transcribe_audio's own move.

Both classes are intentionally thin, protocol-only clients:
  - No retry / reconnect. The caller owns connection lifecycle.
  - No audio transcoding. The caller decides sample rate/format and
    matches it on both the mic-capture and playback sides.
  - No business/turn-taking logic. Events are typed dataclasses; the
    caller (soniox_stitched_bridge.py for calls, public_booking_voice_
    stream.py for the browser widget) owns the state machine.

References: Soniox real-time WebSocket APIs. Exact field names are pinned
here against the API version in production; if Soniox changes the
schema, this file is the choke point for both callers.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Optional, Union

import websockets
from websockets.client import WebSocketClientProtocol as ClientConnection

logger = logging.getLogger(__name__)

SONIOX_STT_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
SONIOX_TTS_URL = "wss://tts-rt.soniox.com/tts-websocket"

# Error codes after which a Soniox socket cannot be used again: bad or missing key (401), key not allowed (403), connection limit (429).
# (Soniox checks the key about a second after the socket opens, so these arrive as a frame, not as a failed connect.)
_CONNECTION_FATAL_CODES = frozenset({401, 403, 429})


def _header_kwargs(headers: dict) -> dict:
    """The keyword that carries extra upgrade headers into `websockets.connect`, by library generation: `extra_headers` up to 13.x (the
    legacy client, which is what generated apps pin: websockets>=12,<13) and `additional_headers` from 14.0 (the repo's own environment).
    Passing the wrong one is a TypeError on every connect, i.e. no speech at all (2026-10-09)."""
    major = int(websockets.__version__.split(".")[0])
    return {"additional_headers": headers} if major >= 14 else {"extra_headers": headers}


def _auth_headers(api_key: Optional[str]) -> dict:
    """The WebSocket upgrade headers that authenticate to Soniox: `Authorization: Bearer <key>`.

    Soniox is retiring the key in the first message (refused with a 401 after 2027-01-15). Never send the key in both places: Soniox answers 400
    to two credentials and refuses a first-message key that differs from the header's. A blank key is a configuration error and fails here,
    loudly, instead of as a 401 a second later. The key is never logged."""
    if not api_key or not str(api_key).strip():
        raise ValueError("Soniox API key is missing: set SONIOX_API_KEY (or the soniox_api_key secret)")
    return {"Authorization": f"Bearer {api_key}"}


# ---- STT: typed events -----------------------------------------------------

def _segment(raw_text: Optional[str], text: str) -> str:
    """One event's piece of a running transcript, ready for `"".join(...)`.

    Soniox tokens carry their own leading space (" doctor"), or none when a word or a mark continues the previous
    token ("19" then "69", "appointment" then "."). `text` is stripped, so it has lost that; `raw_text` keeps it.
    An event built without `raw_text` has no such information and is treated as a separate word, as it always was.
    """
    return raw_text if raw_text is not None else f" {text}"


@dataclass
class STTPartialTranscript:
    """Interim transcript; may be revised by a later final."""
    text: str
    is_final: bool = False
    language: Optional[str] = None
    raw_text: Optional[str] = None   # `text` before stripping: where the spaces fall

    @property
    def segment(self) -> str:
        return _segment(self.raw_text, self.text)


@dataclass
class STTFinalTranscript:
    """Stable transcript segment; not revised."""
    text: str
    is_final: bool = True
    language: Optional[str] = None
    raw_text: Optional[str] = None   # `text` before stripping: where the spaces fall

    @property
    def segment(self) -> str:
        return _segment(self.raw_text, self.text)


@dataclass
class STTSpeechStarted:
    """Speech onset detected by Soniox endpointing."""
    at_ms: int = 0


@dataclass
class STTSpeechEnded:
    """End-of-utterance endpoint detected by Soniox endpointing."""
    at_ms: int = 0


@dataclass
class STTError:
    """Terminal or non-terminal error from the Soniox side."""
    message: str
    code: Optional[str] = None
    fatal: bool = False


STTEvent = Union[
    STTPartialTranscript,
    STTFinalTranscript,
    STTSpeechStarted,
    STTSpeechEnded,
    STTError,
]


class SonioxSTTClient:
    """Async WebSocket client for Soniox streaming STT.

    Usage:
        client = SonioxSTTClient(api_key=..., model=..., language="en")
        await client.connect()
        async def feeder():
            async for pcm_frame in audio_source():
                await client.send_audio(pcm_frame)
            await client.finalize()

        task = asyncio.create_task(feeder())
        async for event in client.events():
            ... dispatch ...
        await task
        await client.close()
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        language: str = "en",
        sample_rate: int = 8000,
        language_hints: Optional[List[str]] = None,
        enable_language_identification: bool = False,
    ):
        """`language_hints`/`enable_language_identification` are additive,
        added 2026-09-30 for the public booking chat's multi-language
        support -- the phone bridge (soniox_stitched_bridge.py) constructs
        this client with only `language`, unchanged, and gets the exact same
        single-language behavior as before: `language_hints` defaults to
        `[language]` and `enable_language_identification` defaults to False.
        When a caller passes a real `language_hints` list (multiple codes),
        Soniox auto-detects among them and tags each token with the language
        it recognized (see STTFinalTranscript.language / _parse_event)."""
        if not api_key:
            raise ValueError("SonioxSTTClient requires api_key")
        self._api_key = api_key
        self._model = model
        self._language = language
        self._sample_rate = sample_rate
        self._language_hints = language_hints
        self._enable_language_identification = enable_language_identification
        self._ws: Optional[ClientConnection] = None
        self._closed = False
        # Track whether we've observed speech-active state so speech-started/ended
        # events can be synthesized from Soniox's word-level flags if needed.
        self._speech_active = False

    async def connect(self) -> None:
        """Open the WS and send the initial config frame."""
        if self._ws is not None:
            raise RuntimeError("SonioxSTTClient.connect() called twice")

        try:
            self._ws = await websockets.connect(
                SONIOX_STT_URL,
                **_header_kwargs(_auth_headers(self._api_key)),
                max_size=None,  # transcripts can be large; don't cap
                ping_interval=20,
                ping_timeout=20,
            )
        except Exception as e:
            logger.warning("Soniox STT connect() failed to open WS to %s: %r",
                           SONIOX_STT_URL, e)
            raise

        # Soniox config message. Field names follow the real-time STT API.
        # `include_nonfinal=true` gives us partial transcripts for early barge-in.
        # `enable_endpoint_detection=true` gives us speech-started / speech-ended.
        config = {
            "model": self._model,
            "audio_format": "pcm_s16le",
            "sample_rate": self._sample_rate,
            "num_channels": 1,
            "language_hints": self._language_hints or [self._language],
            "enable_language_identification": self._enable_language_identification,
            "include_nonfinal": True,
            "enable_endpoint_detection": True,
        }
        try:
            await self._ws.send(json.dumps(config))
        except Exception as e:
            logger.warning("Soniox STT connect() failed to send config frame: %r", e)
            raise
        logger.warning("Soniox STT connected (model=%s, lang=%s, sr=%d)",
                       self._model, self._language, self._sample_rate)

    async def send_audio(self, pcm16_bytes: bytes) -> None:
        """Send one binary PCM16 audio frame. Non-blocking beyond WS buffering."""
        if self._ws is None or self._closed:
            raise RuntimeError("send_audio on closed/unopened STT WS")
        if not pcm16_bytes:
            return
        await self._ws.send(pcm16_bytes)

    async def finalize(self) -> None:
        """Signal end-of-audio. Soniox flushes any pending final transcripts."""
        if self._ws is None or self._closed:
            return
        # Empty string message is the documented end-of-stream signal.
        try:
            await self._ws.send("")
        except Exception as e:
            logger.debug("STT finalize send failed (already closed?): %s", e)

    async def events(self) -> AsyncIterator[STTEvent]:
        """Yield typed STT events until the peer closes or close() is called."""
        if self._ws is None:
            raise RuntimeError("events() called before connect()")
        raw_count = 0
        try:
            async for raw in self._ws:
                raw_count += 1
                # Dump the first 15 raw messages so we can reverse-engineer
                # Soniox's actual wire format (docs are imprecise).
                if raw_count <= 15:
                    preview = raw if isinstance(raw, str) else f"<{len(raw)} bytes>"
                    logger.warning(
                        "STT raw[%d]: %s",
                        raw_count,
                        (preview[:500] if isinstance(preview, str) else preview),
                    )
                event = self._parse_event(raw)
                if event is None:
                    continue
                if isinstance(event, list):
                    for e in event:
                        yield e
                else:
                    yield event
        except websockets.exceptions.ConnectionClosed as e:
            if e.code not in (1000, 1001):
                yield STTError(message=f"STT WS closed: {e}", fatal=True)

    def _parse_event(self, raw: Union[str, bytes]) -> Union[None, STTEvent, list[STTEvent]]:
        """Map a raw Soniox message to one or more typed events.

        Soniox emits JSON messages. The exact shape of the real-time API may
        evolve; we defensively parse the fields we rely on and skip the rest.
        """
        if isinstance(raw, bytes):
            # Soniox STT real-time is JSON over text; ignore unexpected binary.
            return None

        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            logger.debug("STT: non-JSON message ignored")
            return None

        # Soniox's own error frame is flat: {"tokens": [], ..., "error_code": 401, "error_type": "unauthenticated", "error_message": "..."}.
        # It must never be read as an empty transcript (a rejected key would then look like silence). The server closes after it.
        if "error_code" in msg or "error_message" in msg:
            code = msg.get("error_code")
            kind = msg.get("error_type")
            return STTError(
                message=f"{code} {kind}: {msg.get('error_message') or 'unknown error'}" if code else str(msg.get("error_message")),
                code=str(code) if code is not None else None,
                fatal=True,
            )

        # Error envelope
        if "error" in msg:
            err = msg["error"]
            if isinstance(err, dict):
                return STTError(
                    message=str(err.get("message") or err),
                    code=err.get("code"),
                    fatal=bool(err.get("fatal", False)),
                )
            return STTError(message=str(err), fatal=False)

        events: list[STTEvent] = []

        # Tokens / text. Soniox streams segments with is_final flags.
        # Soniox signals end-of-utterance with a special token where
        # text == "<end>" and is_final == true. We treat that as a
        # speech_ended event and exclude it from transcript text.
        tokens = msg.get("tokens") or msg.get("results") or []
        text_parts_final: list[str] = []
        text_parts_partial: list[str] = []
        saw_end_token = False
        # Per-token "language" field (multi-language / enable_language_
        # identification support, added 2026-09-30) -- Soniox tags each
        # recognized token with the language it detected. An utterance is
        # effectively one language in practice, so the LAST non-null value
        # seen wins; this is exposed on the transcript event rather than
        # inferred separately so callers never have to re-derive it.
        final_language: Optional[str] = None
        partial_language: Optional[str] = None
        for t in tokens:
            text = t.get("text", "")
            if not text:
                continue
            if text == "<end>" and t.get("is_final"):
                saw_end_token = True
                continue
            token_language = t.get("language")
            if t.get("is_final"):
                text_parts_final.append(text)
                if token_language:
                    final_language = token_language
            else:
                text_parts_partial.append(text)
                if token_language:
                    partial_language = token_language

        # Some Soniox payloads provide top-level text + is_final directly.
        top_text = msg.get("text")
        if top_text and not tokens:
            if msg.get("is_final"):
                text_parts_final.append(top_text)
                final_language = final_language or msg.get("language")
            else:
                text_parts_partial.append(top_text)
                partial_language = partial_language or msg.get("language")

        raw_final = "".join(text_parts_final)
        raw_partial = "".join(text_parts_partial)
        final_text = raw_final.strip()
        partial_text = raw_partial.strip()

        # Emit speech_started before any transcript text for this message.
        event_name = msg.get("event") or msg.get("type")
        if event_name in ("speech_start", "speech_started"):
            events.append(STTSpeechStarted(at_ms=int(msg.get("at_ms", 0))))
            self._speech_active = True

        # Transcript events come before end-of-speech signals so the caller
        # has final text accumulated before it reacts to speech_ended and
        # advances the turn.
        if final_text:
            events.append(STTFinalTranscript(text=final_text, language=final_language, raw_text=raw_final))
        if partial_text:
            events.append(STTPartialTranscript(text=partial_text, language=partial_language, raw_text=raw_partial))

        # End-of-speech signals (after transcript). Three shapes supported:
        #   1) Explicit `event`: "speech_end"/"speech_ended".
        #   2) Legacy `endpoint` = true on a final token.
        #   3) Soniox real-time endpointing token: text == "<end>" + is_final.
        if event_name in ("speech_end", "speech_ended"):
            events.append(STTSpeechEnded(at_ms=int(msg.get("at_ms", 0))))
            self._speech_active = False
        elif msg.get("endpoint") and final_text:
            events.append(STTSpeechEnded(at_ms=int(msg.get("at_ms", 0))))
            self._speech_active = False
        elif saw_end_token:
            events.append(STTSpeechEnded(at_ms=int(msg.get("at_ms", 0))))
            self._speech_active = False

        if not events:
            return None
        return events[0] if len(events) == 1 else events

    async def close(self) -> None:
        """Close the WebSocket. Safe to call multiple times."""
        if self._closed:
            return
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as e:
                logger.debug("STT close error (ignored): %s", e)
            finally:
                self._ws = None


# ---- TTS: typed events ------------------------------------------------------

@dataclass
class TTSAudioChunk:
    """One audio chunk from the current synthesis. PCM16 little-endian."""
    synthesis_id: str
    pcm16_bytes: bytes


@dataclass
class TTSSynthesisComplete:
    """Marks the end of a synthesis. No more audio for `synthesis_id`."""
    synthesis_id: str


@dataclass
class TTSError:
    """Synthesis-level error. May be fatal (connection lost) or per-request."""
    message: str
    synthesis_id: Optional[str] = None
    fatal: bool = False


TTSEvent = Union[TTSAudioChunk, TTSSynthesisComplete, TTSError]


class SonioxTTSClient:
    """Async WebSocket client for Soniox streaming TTS.

    Wire protocol (per https://soniox.com/docs/tts/rt):
      Config:      {"model", "language", "voice",   (the key is the connection's Authorization header, not a field)
                    "audio_format": "pcm_s16le", "sample_rate",
                    "stream_id": "<default>"}
      Synthesize:  {"text": "...", "text_end": true, "stream_id": "..."}
      Audio:       {"audio": "<base64>", "audio_end": false, "stream_id": "..."}
      Complete:    {"terminated": true, "stream_id": "..."}
      Cancel:      {"stream_id": "...", "cancel": true}
      Error:       {"stream_id": "...", "error_code": int, "error_message": str}

    Usage:
        client = SonioxTTSClient(api_key=..., voice=..., model=...)
        await client.connect()
        sid = await client.synthesize("Hello, how can I help?")
        async for event in client.events():
            if isinstance(event, TTSAudioChunk) and event.synthesis_id == sid:
                ... deliver audio chunk to the caller as it arrives ...
            elif isinstance(event, TTSSynthesisComplete) and event.synthesis_id == sid:
                break

    Barge-in:
        await client.cancel()   # drops the current synthesis on the server
    """

    def __init__(
        self,
        api_key: str,
        voice: str,
        model: str,
        sample_rate: int = 8000,
        language: str = "en",
    ):
        if not api_key:
            raise ValueError("SonioxTTSClient requires api_key")
        self._api_key = api_key
        self._voice = voice
        self._model = model
        self._sample_rate = sample_rate
        self._language = language
        self._ws: Optional[ClientConnection] = None
        self._closed = False
        # The current (in-flight) stream_id. Each synthesize() call starts a
        # brand-new stream on the server by sending a full config message
        # with a fresh stream_id; stream_ids are NOT reusable across
        # utterances (per Soniox docs: "unique among active streams on one
        # socket"). The previous stream must be terminated (or canceled)
        # before or during the next synthesize.
        self._current_synthesis_id: Optional[str] = None

    async def connect(self) -> None:
        """Open the WebSocket. The first config message is sent lazily in
        synthesize() because each Soniox TTS stream is opened by its own
        config message — we send one config per utterance."""
        if self._ws is not None:
            raise RuntimeError("SonioxTTSClient.connect() called twice")
        try:
            self._ws = await websockets.connect(
                SONIOX_TTS_URL,
                **_header_kwargs(_auth_headers(self._api_key)),
                max_size=None,
                ping_interval=20,
                ping_timeout=20,
            )
        except Exception as e:
            logger.warning("Soniox TTS connect() failed to open WS to %s: %r",
                           SONIOX_TTS_URL, e)
            raise
        logger.warning(
            "Soniox TTS WS opened (model=%s, voice=%s, lang=%s, sr=%d)",
            self._model, self._voice, self._language, self._sample_rate,
        )

    async def begin_stream(self, language: Optional[str] = None) -> str:
        """Open a new Soniox TTS stream. Returns the stream_id.

        Soniox's protocol opens a stream via a config message and closes it
        when the caller sends a payload with `text_end=True`. Per Soniox's
        docs, stream_ids are "unique among active streams on one socket" —
        only one stream may be active at a time on a given WebSocket.

        Use this when you want to stream text deltas into a single
        utterance (e.g., an LLM's token stream for one assistant turn).
        Follow with `send_text_chunk()` calls, and finalize with
        `finalize_stream()` — NOT a new `begin_stream()`.

        `language` (added 2026-09-30, optional, defaults to the constructor's
        `language`): lets one long-lived client speak a different language
        per reply -- e.g. the public booking chat detects the patient's
        spoken language per-utterance (via SonioxSTTClient's own per-token
        `language`) and replies in kind, without opening a whole new TTS
        connection just to change language.
        """
        if self._ws is None or self._closed:
            raise RuntimeError("begin_stream on closed/unopened TTS WS")
        synthesis_id = uuid.uuid4().hex
        self._current_synthesis_id = synthesis_id
        config = {
            "model": self._model,
            "language": language or self._language,
            "voice": self._voice,
            "audio_format": "pcm_s16le",
            "sample_rate": self._sample_rate,
            "stream_id": synthesis_id,
        }
        await self._ws.send(json.dumps(config))
        return synthesis_id

    async def send_text_chunk(self, text: str, synthesis_id: str) -> None:
        """Append a text chunk to an open stream. Does NOT finalize.

        Sends `{"text": ..., "text_end": false, "stream_id": ...}`. Safe to
        call repeatedly on the same stream; Soniox buffers text and emits
        audio as soon as it has enough context.
        """
        if self._ws is None or self._closed:
            raise RuntimeError("send_text_chunk on closed/unopened TTS WS")
        if not text:
            return
        payload = {
            "text": text,
            "text_end": False,
            "stream_id": synthesis_id,
        }
        await self._ws.send(json.dumps(payload))

    async def finalize_stream(self, synthesis_id: str) -> None:
        """Signal end-of-text for a stream. Server flushes remaining audio
        and terminates the stream (emits a `terminated` / `audio_end` frame).

        Must be called before opening another stream on this WebSocket —
        otherwise you violate Soniox's "one active stream per socket" rule
        and audio from the two streams interleaves on the wire.
        """
        if self._ws is None or self._closed:
            return
        payload = {
            "text": "",
            "text_end": True,
            "stream_id": synthesis_id,
        }
        await self._ws.send(json.dumps(payload))

    async def synthesize(self, text: str, language: Optional[str] = None) -> str:
        """One-shot convenience: begin + send + finalize. Returns stream_id.

        Equivalent to `begin_stream()` + `send_text_chunk()` +
        `finalize_stream()`. Use for a single fixed string. For streaming an
        LLM's own token-by-token output use the three primitives directly so
        all deltas land on ONE stream. `language` overrides the constructor's
        default for this one synthesis -- see `begin_stream`'s docstring.
        """
        synthesis_id = await self.begin_stream(language=language)
        await self.send_text_chunk(text, synthesis_id)
        await self.finalize_stream(synthesis_id)
        return synthesis_id

    async def cancel(self) -> None:
        """Cancel the current synthesis.

        Sends a cancel message if there is an in-flight synthesis. Safe
        to call speculatively; if nothing is in flight this is a no-op.
        The caller should also stop forwarding any already-buffered audio
        frames tagged with the canceled synthesis_id.
        """
        if self._ws is None or self._closed:
            return
        sid = self._current_synthesis_id
        if not sid:
            return
        cancel_msg = {
            "stream_id": sid,
            "cancel": True,
        }
        try:
            await self._ws.send(json.dumps(cancel_msg))
        except Exception as e:
            logger.debug("TTS cancel send failed (will fall back to close): %s", e)

    async def events(self) -> AsyncIterator[TTSEvent]:
        """Yield typed TTS events until the peer closes or close() is called."""
        if self._ws is None:
            raise RuntimeError("events() called before connect()")
        raw_count = 0
        try:
            async for raw in self._ws:
                raw_count += 1
                # Dump the first 15 raw messages so we can reverse-engineer
                # Soniox's actual TTS wire format (docs are imprecise).
                if raw_count <= 15:
                    if isinstance(raw, (bytes, bytearray)):
                        preview = f"<binary {len(raw)} bytes>"
                    else:
                        preview = raw[:500] if isinstance(raw, str) else str(raw)[:500]
                    logger.warning("TTS raw[%d]: %s", raw_count, preview)
                event = self._parse_event(raw)
                if event is not None:
                    yield event
        except websockets.exceptions.ConnectionClosed as e:
            if e.code not in (1000, 1001):
                yield TTSError(message=f"TTS WS closed: {e}", fatal=True)

    def _parse_event(self, raw: Union[str, bytes]) -> Optional[TTSEvent]:
        """Map a Soniox TTS JSON frame to a typed event.

        Soniox TTS returns JSON messages containing base64-encoded audio,
        termination markers, or errors. Binary WebSocket frames are not
        used by the current protocol but we handle them defensively.
        """
        if isinstance(raw, (bytes, bytearray)):
            # Not expected under current Soniox protocol; ignore.
            return None

        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            logger.debug("TTS: non-JSON text frame ignored")
            return None

        sid = msg.get("stream_id") or self._current_synthesis_id or ""

        # Error envelope: {"stream_id", "error_code", "error_message"}
        if "error_code" in msg or "error_message" in msg or "error" in msg:
            code = msg.get("error_code")
            message = msg.get("error_message") or msg.get("error") or "unknown error"
            # 4xx are typically per-request; 5xx, a missing code, and a rejected key / full connection limit are fatal.
            fatal = bool(code and (int(code) >= 500 or int(code) in _CONNECTION_FATAL_CODES)) or "error" in msg and not code
            return TTSError(
                message=f"{code}: {message}" if code else str(message),
                synthesis_id=sid or None,
                fatal=fatal,
            )

        # Audio frame: {"audio": "<base64>", "audio_end": bool, "stream_id"}
        if "audio" in msg:
            audio_b64 = msg.get("audio")
            if not audio_b64:
                # empty audio chunk — skip
                if msg.get("audio_end"):
                    return TTSSynthesisComplete(synthesis_id=sid)
                return None
            try:
                audio = base64.b64decode(audio_b64)
            except Exception as e:
                logger.debug("TTS: failed to b64-decode audio: %s", e)
                return None
            # Note: if audio_end is true, this is the final audio chunk; the
            # server may still send a separate {"terminated": true} after.
            # We emit the chunk here and let the terminated event mark end.
            return TTSAudioChunk(
                synthesis_id=sid,
                pcm16_bytes=audio,
            )

        # Termination marker: {"terminated": true, "stream_id": "..."}
        if msg.get("terminated"):
            return TTSSynthesisComplete(synthesis_id=sid)

        # audio_end with no audio payload is also an end-of-stream signal
        if msg.get("audio_end") and "audio" not in msg:
            return TTSSynthesisComplete(synthesis_id=sid)

        return None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as e:
                logger.debug("TTS close error (ignored): %s", e)
            finally:
                self._ws = None
