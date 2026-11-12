"""The two endpoints Vonage needs, on one port, and nothing else.

  GET|POST /answer?key=...&uuid=...&from=...   Vonage asks what to do with an inbound call. Only a request carrying the answer key is answered. The reply is the NCCO telling
                              it to stream the call's audio to our websocket, at an address carrying a short-lived signed token.
  websocket /ws/<uuid>?...    The call's audio (PCM16 8 kHz both ways), run through the voice bridge. Refused without a valid token.

Every other path is refused. There are no pages and no other routes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import websockets
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from .audio import play_pcm
from .calllog import CallLog, ended_by, turns_from_transcript
from .bridge import BridgeSettings, ToolFunction, VoiceBridge
from .config import Config
from .coverage import Fixture, make_tools
from .limits import Limits
from .persona import DISCLOSURE, build_system_prompt, call_reference
from .refusals import Refusals
from .soniox_ncco import BRIDGE_PATH, build_ncco, inbound_ws_url
from .tokens import CallTokens, key_matches

logger = logging.getLogger(__name__)

ANSWER_PATH = "/answer"
_CALL_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_WS_PATH = re.compile(r"^" + re.escape(BRIDGE_PATH) + r"/([A-Za-z0-9-]{1,64})$")

Tools = Dict[str, Tuple[Dict[str, Any], ToolFunction]]


class WebsocketCall:
    """The bridge's CallSocket over a websocket connection: audio is binary messages; text messages are Vonage's control frames and carry no audio."""

    def __init__(self, connection: ServerConnection):
        self._ws = connection

    async def receive_audio(self) -> Optional[bytes]:
        while True:
            try:
                message = await self._ws.recv()
            except ConnectionClosed:
                return None
            if isinstance(message, (bytes, bytearray)):
                return bytes(message)

    async def send_audio(self, data: bytes) -> None:
        await self._ws.send(data)

    async def close(self) -> None:
        await self._ws.close(code=1000)


class FakePayerServer:
    def __init__(self, config: Config, tools: Optional[Tools] = None, system_prompt: Optional[str] = None, limits: Optional[Limits] = None, refusals: Optional[Refusals] = None):
        """By default the line answers from the fixture with the persona's prompt (which carries the call's own reference number). `tools` and `system_prompt` replace them in tests."""
        self._config = config
        self._tools: Tools = tools if tools is not None else make_tools(Fixture.load())
        self._system_prompt = system_prompt
        self._tokens = CallTokens(config.answer_key)
        self._limits = limits or Limits(config)
        self._refusals = refusals or Refusals()
        self._active_calls: set = set()
        self._log = CallLog(config.call_log_dir)

    # ---- HTTP: the answer request, and a refusal for everything else ------

    def process_request(self, connection: ServerConnection, request: Request) -> Optional[Response]:
        parts = urlsplit(request.path)
        query = parse_qs(parts.query)
        if "websocket" in request.headers.get("Upgrade", "").lower():
            match = _WS_PATH.match(parts.path)
            if not match:
                return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
            if self._tokens.verify(match.group(1), query) is None:
                return connection.respond(HTTPStatus.FORBIDDEN, "forbidden\n")           # not a call that came through the answer request
            return None
        if parts.path != ANSWER_PATH:
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        if not key_matches((query.get("key") or [""])[0], self._config.answer_key):
            return connection.respond(HTTPStatus.FORBIDDEN, "forbidden\n")
        call_uuid = (query.get("uuid") or [""])[0]
        if not _CALL_ID.match(call_uuid):
            return connection.respond(HTTPStatus.BAD_REQUEST, "bad request\n")
        host = request.headers.get("Host", "")
        if not host:
            return connection.respond(HTTPStatus.BAD_REQUEST, "bad request\n")
        caller = self._tokens.caller((query.get("from") or [""])[0])
        response = connection.respond(HTTPStatus.OK, json.dumps(build_ncco(inbound_ws_url(host, call_uuid, self._tokens.issue(call_uuid, caller)))))
        del response.headers["Content-Type"]                 # respond() set text/plain; assigning would add a second header, not replace it
        response.headers["Content-Type"] = "application/json"
        return response

    # ---- websocket: one call ----------------------------------------------

    async def handle_call(self, connection: ServerConnection) -> None:
        parts = urlsplit(connection.request.path)
        match = _WS_PATH.match(parts.path)
        call_uuid = match.group(1) if match else "unknown"
        caller = self._tokens.verify(call_uuid, parse_qs(parts.query))
        call = WebsocketCall(connection)
        if caller is None or call_uuid in self._active_calls:         # the token was checked at the upgrade; a second stream for a call already running is not allowed
            await call.close()
            return
        decision, handle = self._limits.admit(caller.key)
        if not decision.allowed:
            logger.info("call %s refused (%s) caller=***%s", call_uuid, decision.reason, caller.last4)
            self._write_log(call_uuid, caller.last4, f"refused_{decision.reason}")
            audio = self._refusals.audio.get(decision.reason or "")
            if audio:
                try:
                    await play_pcm(call, audio)
                except Exception as exc:
                    logger.debug("spoken refusal failed (best-effort): %s", exc)
            await call.close()
            return
        self._active_calls.add(call_uuid)
        settings = BridgeSettings(
            call_uuid=call_uuid,
            greeting=DISCLOSURE,
            soniox_api_key=self._config.soniox_api_key,
            stt_model=self._config.stt_model,
            tts_model=self._config.tts_model,
            tts_voice=self._config.tts_voice,
            max_call_seconds=self._config.max_call_seconds,
            time_up_audio=self._refusals.audio.get("time"),
        )
        bridge = VoiceBridge(settings, self._tools, self._system_prompt if self._system_prompt is not None else build_system_prompt(call_uuid))
        try:
            await bridge.run(call)
        except Exception:
            logger.exception("call %s ended with an error", call_uuid)
        finally:
            self._limits.release(handle)
            self._active_calls.discard(call_uuid)
            self._write_log(call_uuid, caller.last4, ended_by(bridge.terminated_by), bridge.duration_seconds, turns_from_transcript(bridge.transcript))

    def _write_log(self, call_uuid: str, last4: str, ended: str, duration: float = 0.0, turns=None) -> None:
        try:
            self._log.write(reference=call_reference(call_uuid), last4=last4, ended=ended, duration_seconds=duration, turns=turns)
        except Exception:
            logger.exception("could not write the log entry for call %s", call_uuid)

    def cleanup_log(self) -> int:
        return self._log.cleanup(self._config.log_retention_days)

    def serve(self, host: Optional[str] = None, port: Optional[int] = None):
        """An async context manager running the server: ``async with server.serve(...)``."""
        return serve(self.handle_call, host or self._config.listen_host, port if port is not None else self._config.port, process_request=self.process_request)
