# Copied from agent_framework b227c23b: modules/communication/backend/soniox_stitched_bridge.py
# Changes: when the model asks for a lookup before it has said anything, a stored "one moment" is played at once (the lookup and the second model call take a second or
# more); how long each reply took to start is measured; audio goes to the caller at the pace it is heard (never more than a fraction of a second ahead), and the agent counts as speaking until it has played, so a
# caller breaking in cuts a reply off (the original sent each reply as fast as it was synthesized, which sits in the telephony provider's buffer where a barge-in cannot reach);
# at the call-length limit the watchdog first plays a stored "time is up" message; tool results are kept whole in the transcript (the original cut them to 500 characters);
# the privacy-redaction step and the placeholder helpers, the tenant and practice configuration, call persistence, the payer-call control, the language hints
# and the platform's tool dispatcher are removed. The call's audio goes through a small CallSocket (below) where the original took the web framework's websocket, so the
# server that carries it can be any. Tools are a name -> (definition, async function) mapping run in this file. The turn state machine, barge-in, streaming reply into
# speech, the greeting and the call-length watchdog are as they were.
"""
Voice bridge: one phone call, three legs.

    Vonage audio (PCM16 8 kHz) <-> bridge <-> Soniox speech-to-text (PCM16 8 kHz)
                                          <-> Claude streaming (with tools)
                                          <-> Soniox text-to-speech (PCM16 8 kHz)

Vonage delivers linear 16-bit PCM at 8 kHz (see soniox_ncco.CONTENT_TYPE) and Soniox speaks the same format, so audio passes through untouched.

The bridge:
  - sends the caller's audio to speech-to-text and turns its endpointing into turns;
  - on end of the caller's turn, streams the text into Claude, runs any tool it asks for, and feeds the result back;
  - streams Claude's words into text-to-speech and plays the audio to the caller;
  - on new caller speech while it is speaking, cancels speech and the model (barge-in);
  - hangs up when the call reaches its maximum length (watchdog).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Tuple

from .anthropic_streaming_client import StreamedAssistantMessage, ToolUseCall, run_claude_turn
from .audio import play_pcm
from .tone import DialToneDetector, dial_tone_pcm
from .soniox_streaming_client import (
    SonioxSTTClient,
    SonioxTTSClient,
    STTError,
    STTFinalTranscript,
    STTPartialTranscript,
    STTSpeechEnded,
    STTSpeechStarted,
    TTSAudioChunk,
    TTSError,
    TTSSynthesisComplete,
)

logger = logging.getLogger(__name__)

BYTES_PER_SECOND = 16000        # PCM16 at 8 kHz
FRAME_BYTES = 320               # 20 ms
PLAYOUT_LEAD_SECONDS = 0.2      # how far ahead of what the caller is hearing audio may be sent: the most a barge-in cannot take back

ToolFunction = Callable[[Dict[str, Any]], Awaitable[Any]]


class CallSocket(Protocol):
    """The call's audio as the bridge sees it. Whatever carries the Vonage websocket provides these three."""

    async def receive_audio(self) -> Optional[bytes]:
        """The next block of the caller's audio, or None when the caller has gone. Control messages that carry no audio are skipped inside."""

    async def send_audio(self, data: bytes) -> None:
        """Play audio to the caller."""

    async def close(self) -> None:
        """Hang up."""


HANG_UP_AFTER_SPEECH_SECONDS = 1.0             # the line stays open this long after the last word, as a person would put the phone down
HANG_UP_TONE_SECONDS = 3.0                     # after it hangs up, the caller hears a dial tone until they hang up too, for at most this long
HANG_UP_PATIENCE_SECONDS = 30.0                # but never waits longer than this for the goodbye to play

END_CALL_TOOL: Dict[str, Any] = {
    "name": "end_call",
    "description": "Hang up. Use it only after the caller has said they need nothing more, and after you have said a short goodbye in the same turn.",
    "input_schema": {"type": "object", "properties": {}},
}


class BridgeState(str, Enum):
    CONNECTING = "connecting"
    LISTENING = "listening"            # waiting for caller speech
    CALLER_SPEAKING = "caller_speaking"
    THINKING = "thinking"              # Claude turn in progress
    AGENT_SPEAKING = "agent_speaking"  # TTS audio being played to caller
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass
class BridgeSettings:
    call_uuid: str
    greeting: str                       # the words spoken first, before the caller says anything
    soniox_api_key: str
    stt_model: str = "stt-rt-preview"
    tts_model: str = "tts-rt-v1"
    tts_voice: str = "Adrian"
    max_call_seconds: float = 300
    language: str = "en"
    time_up_audio: Optional[bytes] = None      # said, in the same voice, just before the line hangs up at the length limit
    hold_audio: Optional[bytes] = None         # "one moment while I look that up": played at once when the model asks for a lookup before it has said anything
    hold_text: str = ""                        # what hold_audio says, for the transcript
    ring_audio: Optional[bytes] = None         # the ringing the caller hears before the line answers
    reply_delay_seconds: float = 0.0           # the least time from the caller finishing a question to the first sound of the reply
    answer_delay_seconds: float = 0.0          # silence after the ringing, before the greeting


@dataclass
class TurnRecord:
    """One round-trip in the call's transcript history."""
    role: str  # "user" | "assistant" | "tool_result"
    text: str
    tool_uses: List[Dict[str, Any]] = field(default_factory=list)
    at_s: float = field(default_factory=time.monotonic)


class VoiceBridge:
    """Three-leg voice bridge orchestrator.

    Lifetime: one instance per call. Not reusable across calls.
    """

    def __init__(self, settings: BridgeSettings, tools: Dict[str, Tuple[Dict[str, Any], ToolFunction]], system_prompt: str):
        self._cfg = settings
        self._tools = tools
        self._tool_defs_anthropic = [definition for (definition, _function) in tools.values()] + [END_CALL_TOOL]
        self._system_prompt = system_prompt

        self._stt: Optional[SonioxSTTClient] = None
        self._tts: Optional[SonioxTTSClient] = None

        # Conversation history in Anthropic messages format.
        self._messages: List[Dict[str, Any]] = []
        # Human-readable transcript.
        self._transcript: List[TurnRecord] = []

        # Current turn's buffering.
        self._pending_user_text: List[str] = []
        self._caller_language: Optional[str] = None  # last language Soniox identified in the caller's speech
        self._state = BridgeState.CONNECTING
        self._started_at = time.monotonic()

        # Barge-in + cancellation state.
        self._claude_cancel: Optional[asyncio.Event] = None
        self._active_tts_synthesis_id: Optional[str] = None
        # Audio from text-to-speech waits here and is played out at the pace it is heard; `_play_until` is when what has been sent will have finished playing.
        self._playout: asyncio.Queue = asyncio.Queue()
        self._play_until = 0.0
        self._live_ids: set = set()              # audio from these syntheses (and stored phrases) may still be played; a barge-in empties it
        self._held_this_turn = False
        self._holds = 0
        self._call: Optional[CallSocket] = None
        self._dial_tone = DialToneDetector()
        self._ending = False                     # the model has decided the call is over: it hangs up once the goodbye has been heard
        # How long each reply took to begin: from the end-of-speech signal (Soniox raises it about 0.3 s after the caller stops) to the first audio sent.
        self._turn_end_at: Optional[float] = None
        self._reply_latencies: List[float] = []

        # Terminal outcome (stamped at close time).
        self._failure_reason: Optional[str] = None
        self._terminated_by: Optional[str] = None  # "caller" | "watchdog" | "error"

        # Strong references to fire-and-forget background tasks (e.g. the turn-advance task spawned from the STT event loop). Without this, the task can be
        # garbage-collected mid-flight, and any exception raised inside it is silently swallowed by asyncio's default handler instead of failing the call visibly.
        self._background_tasks: set = set()

    async def _record_spoken_greeting(self, greeting: str) -> None:
        """Put the opening line the caller heard into the model's conversation, as its own first turn.

        The greeting goes straight to TTS and never passes through the model, so without this the model does not know what it said: it re-asked a question the
        greeting had asked, greeted twice, and told the caller it "had no record" of giving its name. Claude's API needs the first message to come from the user,
        hence the marker turn. Also stored in the transcript, which would otherwise begin after the greeting.
        """
        self._messages.append({"role": "user", "content": "[The call has connected. You speak first.]"})
        self._messages.append({"role": "assistant", "content": greeting})
        self._transcript.append(TurnRecord(role="assistant", text=greeting))

    # ---- public entrypoint -----------------------------------------------

    async def run(self, call: CallSocket) -> None:
        """Main orchestration entrypoint. Returns when the call ends."""
        logger.info("bridge_start call=%s lang=%s", self._cfg.call_uuid, self._cfg.language)
        try:
            try:
                await self._connect_legs()
            except Exception as e:
                logger.warning("bridge _connect_legs failed for call=%s: %r", self._cfg.call_uuid, e)
                self._terminated_by = "error"
                self._failure_reason = self._failure_reason or f"connect_legs: {e}"
                raise
            self._state = BridgeState.LISTENING
            self._call = call

            if self._cfg.ring_audio:                                  # the line rings before anyone picks up
                await play_pcm(call, self._cfg.ring_audio)
            if self._cfg.answer_delay_seconds > 0:                     # then a moment before it speaks, as a person picking up would take
                await asyncio.sleep(self._cfg.answer_delay_seconds)

            # Initial greeting. The bridge is otherwise purely reactive: it waits for STT speech-ended before doing anything. Without a proactive greeting the
            # caller hears silence and hangs up (observed: 36 s -> caller-terminated, 0 turns).
            try:
                greeting = self._cfg.greeting
                assert self._tts is not None
                self._active_tts_synthesis_id = await self._tts.synthesize(greeting)
                self._live_ids.add(self._active_tts_synthesis_id)
                self._state = BridgeState.AGENT_SPEAKING
                logger.info("bridge initial greeting queued (call=%s synth_id=%s)", self._cfg.call_uuid, self._active_tts_synthesis_id)
            except Exception as e:
                logger.warning("bridge initial greeting failed (call=%s): %r", self._cfg.call_uuid, e)
            else:
                await self._record_spoken_greeting(greeting)

            # Four concurrent tasks:
            #   1) caller audio -> STT
            #   2) STT events -> turn state machine
            #   3) TTS events -> a queue; 3b) the queue -> caller audio, at the pace it is heard (and state)
            #   4) Watchdog: enforce the maximum call length
            audio_in = asyncio.create_task(self._audio_to_stt(call))
            stt_loop = asyncio.create_task(self._stt_event_loop(call))
            tts_loop = asyncio.create_task(self._tts_event_loop(call))
            playout = asyncio.create_task(self._playout_loop(call))
            watchdog = asyncio.create_task(self._watchdog(call))

            done, pending = await asyncio.wait([audio_in, stt_loop, tts_loop, playout, watchdog], return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in pending:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            # Surface any exception from the first-completed task for logs.
            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    logger.exception("Bridge task failed: %s", exc)
                    self._terminated_by = self._terminated_by or "error"

        finally:
            self._state = BridgeState.CLOSING
            await self._teardown()
            self._state = BridgeState.CLOSED
            logger.info(
                "bridge_end call=%s duration_s=%d terminated_by=%s turns=%d failure=%s",
                self._cfg.call_uuid, int(self.duration_seconds), self._terminated_by, len(self._transcript), self._failure_reason,
            )

    # ---- connection / teardown -------------------------------------------

    async def _connect_legs(self) -> None:
        if not self._cfg.soniox_api_key:
            raise RuntimeError("Soniox API key is not configured")
        self._stt = SonioxSTTClient(api_key=self._cfg.soniox_api_key, model=self._cfg.stt_model, language=self._cfg.language, sample_rate=8000)
        self._tts = SonioxTTSClient(api_key=self._cfg.soniox_api_key, voice=self._cfg.tts_voice, model=self._cfg.tts_model, sample_rate=8000)
        logger.info("bridge connecting legs (call=%s stt_model=%s tts_model=%s voice=%s)", self._cfg.call_uuid, self._cfg.stt_model, self._cfg.tts_model, self._cfg.tts_voice)
        await asyncio.gather(self._stt.connect(), self._tts.connect())
        logger.info("bridge connected (call=%s)", self._cfg.call_uuid)

    async def _teardown(self) -> None:
        if self._claude_cancel is not None:
            self._claude_cancel.set()
        if self._stt is not None:
            try:
                await self._stt.close()
            except Exception as exc:
                logger.debug("STT close during teardown failed (best-effort): %s", exc)
        if self._tts is not None:
            try:
                await self._tts.close()
            except Exception as exc:
                logger.debug("TTS close during teardown failed (best-effort): %s", exc)

    # ---- inbound: caller audio -> STT ------------------------------------

    async def _audio_to_stt(self, call: CallSocket) -> None:
        """Read PCM16 audio from the call and push it to Soniox STT. No transcoding: both sides are PCM16 8 kHz."""
        assert self._stt is not None
        frame_count = 0
        byte_count = 0
        try:
            while True:
                data = await call.receive_audio()
                if data is None:
                    logger.info("caller disconnect (call=%s frames=%d bytes=%d)", self._cfg.call_uuid, frame_count, byte_count)
                    self._terminated_by = self._terminated_by or "caller"
                    return
                await self._stt.send_audio(data)
                if self._dial_tone.feed(data):
                    logger.info("dial tone heard: the caller has hung up (call=%s)", self._cfg.call_uuid)
                    self._terminated_by = self._terminated_by or "caller"
                    await self._hang_up(call)
                    return
                frame_count += 1
                byte_count += len(data)
                if frame_count == 1:
                    logger.info("first audio from the caller (call=%s bytes=%d)", self._cfg.call_uuid, len(data))
        except Exception as e:
            logger.exception("audio->stt loop failed: %s", e)
            self._terminated_by = self._terminated_by or "error"
            raise
        finally:
            # Tell STT we're done so it flushes any final transcript.
            try:
                await self._stt.finalize()
            except Exception as exc:
                logger.debug("STT finalize failed (best-effort): %s", exc)

    # ---- inbound event loop: STT -> turn state machine -------------------

    async def _stt_event_loop(self, call: CallSocket) -> None:
        assert self._stt is not None
        async for event in self._stt.events():
            if isinstance(event, STTSpeechStarted):
                # Barge-in: if the agent is currently speaking, cancel everything.
                if self._state in (BridgeState.AGENT_SPEAKING, BridgeState.THINKING):
                    await self._barge_in()
                self._state = BridgeState.CALLER_SPEAKING

            elif isinstance(event, STTPartialTranscript):
                # Length only is ever logged: a transcript is the caller's own words.
                # Partial transcripts may also indicate barge-in mid-response.
                if self._state == BridgeState.AGENT_SPEAKING and event.text:
                    await self._barge_in()
                    self._state = BridgeState.CALLER_SPEAKING

            elif isinstance(event, STTFinalTranscript):
                self._pending_user_text.append(event.segment)
                if event.language:
                    self._caller_language = event.language

            elif isinstance(event, STTSpeechEnded):
                if self._pending_user_text:
                    user_text = "".join(self._pending_user_text).strip()
                    self._pending_user_text.clear()
                    # Advance a turn.
                    self._turn_end_at = time.monotonic()
                    self._state = BridgeState.THINKING
                    self._schedule_advance_turn(user_text)

            elif isinstance(event, STTError):
                logger.warning("STT error: %s (fatal=%s)", event.message, event.fatal)
                if event.fatal:
                    self._terminated_by = self._terminated_by or "error"
                    return

    # ---- outbound event loop: TTS -> caller ------------------------------

    async def _tts_event_loop(self, call: CallSocket) -> None:
        assert self._tts is not None
        async for event in self._tts.events():
            if isinstance(event, TTSAudioChunk):
                # Drop frames from a superseded (canceled) synthesis: simple, deterministic barge-in semantics.
                if event.synthesis_id != self._active_tts_synthesis_id:
                    continue
                self._playout.put_nowait((event.synthesis_id, event.pcm16_bytes))
            elif isinstance(event, TTSSynthesisComplete):
                # Everything of this reply has been generated; it has not necessarily been heard. The playout loop ends the "speaking" state when it has been.
                self._playout.put_nowait((event.synthesis_id, None))
            elif isinstance(event, TTSError):
                logger.warning("TTS error: %s (fatal=%s)", event.message, event.fatal)
                if event.fatal:
                    self._terminated_by = self._terminated_by or "error"
                    return

    async def _playout_loop(self, call: CallSocket) -> None:
        """Send queued speech to the caller at the pace it is heard, staying at most PLAYOUT_LEAD_SECONDS ahead of what they are hearing.

        Soniox synthesizes much faster than real time. Sending each chunk as it arrived put whole replies into the telephony provider's buffer within a second or two:
        the bridge then believed the agent had finished speaking while the caller still heard it, so a caller talking over it was not noticed, and what was already
        buffered could not be recalled. Pacing keeps the bridge's idea of "speaking" true to what the caller hears, and a barge-in drops everything not yet sent.
        """
        while True:
            synthesis_id, data = await self._playout.get()
            if synthesis_id not in self._live_ids:
                continue                                              # cancelled by a barge-in
            if data is None:
                delay = max(0.0, self._play_until - time.monotonic())
                task = asyncio.create_task(self._listening_after(synthesis_id, delay))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
                continue
            if self._turn_end_at is not None and self._cfg.reply_delay_seconds > 0:
                wait = self._turn_end_at + self._cfg.reply_delay_seconds - time.monotonic()
                if wait > 0:                                          # a person takes a moment to answer: the first sound comes this long after the caller stopped
                    await asyncio.sleep(wait)
            for start in range(0, len(data), FRAME_BYTES):
                ahead = self._play_until - time.monotonic()
                if ahead > PLAYOUT_LEAD_SECONDS:
                    await asyncio.sleep(ahead - PLAYOUT_LEAD_SECONDS)
                if synthesis_id not in self._live_ids:
                    break                                             # cut off while waiting
                frame = data[start:start + FRAME_BYTES]
                try:
                    await call.send_audio(frame)
                except Exception as e:
                    logger.info("send to the caller failed (call=%s err=%r)", self._cfg.call_uuid, e)
                    self._terminated_by = self._terminated_by or "caller"
                    return
                now = time.monotonic()
                self._play_until = max(self._play_until, now) + len(frame) / BYTES_PER_SECOND
                if self._turn_end_at is not None:
                    self._reply_latencies.append(now - self._turn_end_at)
                    logger.info("reply_latency_ms=%d (call=%s; from the end-of-speech signal)", int(self._reply_latencies[-1] * 1000), self._cfg.call_uuid)
                    self._turn_end_at = None

    async def _listening_after(self, synthesis_id: str, delay: float) -> None:
        """When a reply has finished playing, go back to listening (unless it was cut off or a newer one has begun)."""
        await asyncio.sleep(delay)
        if synthesis_id == self._active_tts_synthesis_id and self._state == BridgeState.AGENT_SPEAKING:
            self._state = BridgeState.LISTENING

    # ---- barge-in fanout --------------------------------------------------

    async def _barge_in(self) -> None:
        """Cancel in-flight Claude + TTS atomically; reset state to listening."""
        logger.info("barge_in: canceling agent response (call=%s)", self._cfg.call_uuid)
        if self._claude_cancel is not None:
            self._claude_cancel.set()
        # Invalidate the current synthesis so any already-buffered chunks are dropped.
        self._active_tts_synthesis_id = None
        self._live_ids.clear()
        self._turn_end_at = None                                      # the reply being timed was cut off, not started late
        while not self._playout.empty():                              # audio generated but not yet sent is dropped
            self._playout.get_nowait()
        self._play_until = time.monotonic()
        if self._tts is not None:
            try:
                await self._tts.cancel()
            except Exception as e:
                logger.debug("tts.cancel() error (ignored): %s", e)
        self._state = BridgeState.LISTENING

    # ---- turn advancement ------------------------------------------------

    def _schedule_advance_turn(self, user_text: str) -> None:
        """Fire-and-forget `_advance_turn`, but retain the task reference and observe its outcome.

        `_advance_turn` is launched from the synchronous STT event loop via `asyncio.create_task`, so nothing normally awaits it. If the bare task object is
        discarded, (a) it can be garbage-collected mid-flight (asyncio only holds a weak reference), and (b) any exception raised inside it is reported to
        asyncio's default exception handler instead of this module's logger, and `self._state` is left stuck at THINKING (set just before this call), hanging the
        live call forever waiting for a turn that will never complete.
        """
        task = asyncio.create_task(self._advance_turn(user_text))
        self._background_tasks.add(task)

        def _on_done(t: "asyncio.Task") -> None:
            self._background_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error("_advance_turn failed for call=%s (state stuck at %s): %s", self._cfg.call_uuid, self._state, exc, exc_info=exc)
                self._terminated_by = self._terminated_by or "error"
                self._failure_reason = self._failure_reason or f"advance_turn: {exc}"
                # Unstick the state machine so the caller isn't left hanging in THINKING with no turn ever able to complete.
                if self._state == BridgeState.THINKING:
                    self._state = BridgeState.LISTENING

        task.add_done_callback(_on_done)

    async def _advance_turn(self, user_text: str) -> None:
        """Run one Claude turn: stream text to TTS, run tools, loop until no tool_use."""
        if not user_text:
            self._state = BridgeState.LISTENING
            return

        self._transcript.append(TurnRecord(role="user", text=user_text))
        self._messages.append({"role": "user", "content": user_text})

        # Start a fresh cancel event scoped to this turn.
        self._claude_cancel = asyncio.Event()
        self._held_this_turn = False

        # Loop: keep running Claude turns while the model returns tool_use blocks.
        while True:
            assistant = await self._run_one_claude_turn()
            if assistant is None:
                # Canceled by barge-in.
                return

            # Append assistant message to history (only if not canceled).
            self._messages.append({"role": "assistant", "content": assistant.content})
            self._transcript.append(TurnRecord(
                role="assistant",
                text=assistant.text,
                tool_uses=[{"name": tu.name, "input": tu.input} for tu in assistant.tool_uses],
            ))

            tool_uses = assistant.tool_uses
            if not tool_uses:
                # End of turn. A reply that has words is still being spoken (the model finishes long before the caller has heard it): the playout loop returns to
                # listening when it has played. Only a turn with nothing to say hands control back now.
                if not assistant.text.strip():
                    self._state = BridgeState.LISTENING
                if self._ending:
                    self._schedule_hang_up()
                return

            # Run each tool_use and build the follow-up user message.
            tool_result_blocks: List[Dict[str, Any]] = []
            for tu in tool_uses:
                block = await self._run_tool(tu)
                tool_result_blocks.append(block)
                self._transcript.append(TurnRecord(
                    role="tool_result",
                    text=str(block.get("content", "")),
                    tool_uses=[{"name": tu.name, "tool_use_id": tu.id}],
                ))
            self._messages.append({"role": "user", "content": tool_result_blocks})

            # If barge-in happened while a tool ran, stop the loop.
            if self._claude_cancel is not None and self._claude_cancel.is_set():
                return

    async def _run_tool(self, call: ToolUseCall) -> Dict[str, Any]:
        """Run one tool the model asked for and return its tool_result block. An unknown tool or a failing one is reported to the model, not raised: the call goes on."""
        if call.name == END_CALL_TOOL["name"]:
            self._ending = True
            return {"type": "tool_result", "tool_use_id": call.id, "content": json.dumps({"ok": True, "note": "The line closes after your goodbye has been heard. Say nothing more."})}
        entry = self._tools.get(call.name)
        if entry is None:
            return {"type": "tool_result", "tool_use_id": call.id, "content": json.dumps({"error": f"unknown tool {call.name}"}), "is_error": True}
        try:
            result = await entry[1](call.input)
        except Exception as exc:
            logger.warning("tool %s failed (call=%s): %r", call.name, self._cfg.call_uuid, exc)
            return {"type": "tool_result", "tool_use_id": call.id, "content": json.dumps({"error": "the lookup failed"}), "is_error": True}
        content = result if isinstance(result, str) else json.dumps(result, default=str)
        return {"type": "tool_result", "tool_use_id": call.id, "content": content}

    async def _run_one_claude_turn(self) -> Optional[StreamedAssistantMessage]:
        """Execute one Claude streaming call; stream text to TTS as it arrives.

        One TTS stream per assistant turn. Every text delta from Claude is appended to the SAME Soniox stream via `send_text_chunk()`, and the stream is
        finalized at turn end. This preserves streaming-TTS latency (Soniox emits audio as soon as it has enough text) while respecting Soniox's "one active
        stream per socket" rule: opening a new stream per clause (the old behavior) caused overlapping streams to interleave audio and produced mid-word artifacts.
        """
        assert self._tts is not None
        self._state = BridgeState.THINKING

        # Lazy: only open a stream if Claude actually emits text this turn. Tool-only turns produce no audio; don't burn a config roundtrip.
        turn_synthesis_id: Optional[str] = None

        async def send_speech(text: str) -> None:
            nonlocal turn_synthesis_id
            if turn_synthesis_id is None:
                # First text of the turn -- open the stream now. Speak in the language the caller is speaking (None until one has been identified: the voice's
                # own default).
                turn_synthesis_id = await self._tts.begin_stream(language=self._caller_language)
                self._active_tts_synthesis_id = turn_synthesis_id
                self._live_ids.add(turn_synthesis_id)
                self._state = BridgeState.AGENT_SPEAKING
            try:
                await self._tts.send_text_chunk(text, turn_synthesis_id)
            except Exception as e:
                logger.warning("TTS send_text_chunk failed mid-turn: %s", e)

        async def on_text(chunk: str) -> None:
            if chunk:
                await send_speech(chunk)

        async def on_tool_use(call: ToolUseCall) -> None:
            # Tools are run centrally in _advance_turn. The lookup and the second model call take a second or more, so unless the model has already said something,
            # play the stored "one moment" now: the caller hears a person starting to answer, and the answer queues up behind it.
            logger.debug("Claude requested tool: %s", call.name)
            if turn_synthesis_id is None and self._cfg.hold_audio and not self._held_this_turn:
                self._held_this_turn = True
                self._holds += 1
                hold_id = f"hold-{self._holds}"
                self._live_ids.add(hold_id)
                self._state = BridgeState.AGENT_SPEAKING
                self._transcript.append(TurnRecord(role="assistant", text=self._cfg.hold_text))
                self._playout.put_nowait((hold_id, self._cfg.hold_audio))

        try:
            assistant = await run_claude_turn(
                messages=self._messages,
                tools=self._tool_defs_anthropic,
                system_prompt=self._system_prompt,
                on_text=on_text,
                on_tool_use=on_tool_use,
                cancel_event=self._claude_cancel,
            )
        except Exception as e:
            logger.exception("Claude turn failed: %s", e)
            self._terminated_by = self._terminated_by or "error"
            # Best-effort: close the dangling stream so the socket stays tidy.
            if turn_synthesis_id is not None:
                try:
                    await self._tts.finalize_stream(turn_synthesis_id)
                except Exception as exc:
                    logger.debug("TTS stream finalize failed (best-effort): %s", exc)
            return None

        # If canceled (barge-in), _barge_in() already sent cancel() to Soniox and reset _active_tts_synthesis_id. Don't finalize: cancel+finalize would double up
        # on the wire, and the server already dropped the stream.
        if self._claude_cancel is not None and self._claude_cancel.is_set():
            return None

        # Finalize the stream: tells Soniox no more text is coming. The server flushes remaining audio and emits TTSSynthesisComplete, which the TTS event
        # loop uses to transition back to LISTENING.
        if turn_synthesis_id is not None:
            try:
                await self._tts.finalize_stream(turn_synthesis_id)
            except Exception as e:
                logger.warning("TTS finalize_stream failed: %s", e)

        return assistant

    # ---- watchdog --------------------------------------------------------

    async def _watchdog(self, call: CallSocket) -> None:
        """Hard cap per-call duration, when there is one (max_call_seconds above zero). Hangs up when exceeded: a model is not trusted to watch a clock."""
        if self._cfg.max_call_seconds <= 0:
            await asyncio.Event().wait()                             # no limit: the call ends when someone hangs up
        deadline = self._started_at + self._cfg.max_call_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.info("Watchdog triggered: call %s exceeded %ds", self._cfg.call_uuid, self._cfg.max_call_seconds)
                self._terminated_by = "watchdog"
                if self._cfg.time_up_audio:
                    await self._barge_in()                           # stop whatever is being said, then say the time is up
                    try:
                        await play_pcm(call, self._cfg.time_up_audio)
                    except Exception as exc:
                        logger.debug("time-up message failed (best-effort): %s", exc)
                await self._hang_up(call)
                return
            await asyncio.sleep(min(remaining, 5.0))

    def _schedule_hang_up(self) -> None:
        if self._call is None:
            return
        task = asyncio.create_task(self._hang_up_when_the_goodbye_is_heard(self._call))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _hang_up_when_the_goodbye_is_heard(self, call: CallSocket) -> None:
        """The model ended the call: wait until everything it said has been played, then a moment more, and close the line."""
        give_up = time.monotonic() + HANG_UP_PATIENCE_SECONDS
        while time.monotonic() < give_up:
            idle = (self._playout.empty() and self._turn_end_at is None and self._state != BridgeState.THINKING
                    and time.monotonic() >= self._play_until + HANG_UP_AFTER_SPEECH_SECONDS)
            if idle:
                break
            await asyncio.sleep(0.2)
        logger.info("bridge hang-up: the representative ended the call (call=%s)", self._cfg.call_uuid)
        self._terminated_by = self._terminated_by or "hangup"
        if HANG_UP_TONE_SECONDS > 0:
            try:
                await play_pcm(call, dial_tone_pcm(HANG_UP_TONE_SECONDS))       # ends early: when the caller hangs up, the call is over and this task is cancelled
            except Exception as exc:                                                # noqa: BLE001 -- best-effort
                logger.debug("dial tone failed (ignored): %s", exc)
        await self._hang_up(call)

    async def _hang_up(self, call: CallSocket) -> None:
        try:
            await call.close()
        except Exception as exc:
            logger.debug("hang-up failed (best-effort): %s", exc)

    # ---- accessors for the call log --------------------------------------

    @property
    def transcript(self) -> List[TurnRecord]:
        return list(self._transcript)

    @property
    def failure_reason(self) -> Optional[str]:
        return self._failure_reason

    @property
    def terminated_by(self) -> Optional[str]:
        return self._terminated_by

    @property
    def reply_latencies_ms(self) -> List[int]:
        return [int(x * 1000) for x in self._reply_latencies]

    @property
    def duration_seconds(self) -> float:
        return time.monotonic() - self._started_at
