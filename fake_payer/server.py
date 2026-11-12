"""The two endpoints Vonage needs, on one port, and nothing else.

  GET|POST /answer?uuid=...   Vonage asks what to do with an inbound call. The reply is the NCCO telling it to stream the call's audio to our websocket.
  websocket /ws/<uuid>        The call's audio (PCM16 8 kHz both ways), run through the voice bridge.

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

from .bridge import BridgeSettings, ToolFunction, VoiceBridge
from .config import Config
from .persona import DISCLOSURE
from .soniox_ncco import BRIDGE_PATH, build_ncco, inbound_ws_url

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
    def __init__(self, config: Config, tools: Optional[Tools] = None, system_prompt: str = ""):
        self._config = config
        self._tools: Tools = tools or {}
        self._system_prompt = system_prompt

    # ---- HTTP: the answer request, and a refusal for everything else ------

    def process_request(self, connection: ServerConnection, request: Request) -> Optional[Response]:
        if "websocket" in request.headers.get("Upgrade", "").lower():
            return None if _WS_PATH.match(urlsplit(request.path).path) else connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        parts = urlsplit(request.path)
        if parts.path != ANSWER_PATH:
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        query = parse_qs(parts.query)
        call_uuid = (query.get("uuid") or [""])[0]
        if not _CALL_ID.match(call_uuid):
            return connection.respond(HTTPStatus.BAD_REQUEST, "bad request\n")
        host = request.headers.get("Host", "")
        if not host:
            return connection.respond(HTTPStatus.BAD_REQUEST, "bad request\n")
        response = connection.respond(HTTPStatus.OK, json.dumps(build_ncco(inbound_ws_url(host, call_uuid))))
        del response.headers["Content-Type"]                 # respond() set text/plain; assigning would add a second header, not replace it
        response.headers["Content-Type"] = "application/json"
        return response

    # ---- websocket: one call ----------------------------------------------

    async def handle_call(self, connection: ServerConnection) -> None:
        match = _WS_PATH.match(urlsplit(connection.request.path).path)
        call_uuid = match.group(1) if match else "unknown"
        settings = BridgeSettings(
            call_uuid=call_uuid,
            greeting=DISCLOSURE,
            soniox_api_key=self._config.soniox_api_key,
            stt_model=self._config.stt_model,
            tts_model=self._config.tts_model,
            tts_voice=self._config.tts_voice,
            max_call_seconds=self._config.max_call_seconds,
        )
        bridge = VoiceBridge(settings, self._tools, self._system_prompt)
        try:
            await bridge.run(WebsocketCall(connection))
        except Exception:
            logger.exception("call %s ended with an error", call_uuid)

    def serve(self, host: Optional[str] = None, port: Optional[int] = None):
        """An async context manager running the server: ``async with server.serve(...)``."""
        return serve(self.handle_call, host or self._config.listen_host, port if port is not None else self._config.port, process_request=self.process_request)
