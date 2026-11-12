# Copied from agent_framework b227c23b: core/anthropic_streaming_client.py
# Changes: the key lookup, default model and URL, which the original took from the platform's own client module, are defined here;
# the model override variable is FAKE_PAYER_MODEL instead of ORCA_MODEL; the request turns the model's own reasoning off (it added about 0.8 s before the first word).
# Changes from the original are listed in this header; everything else is as it was.
"""
Anthropic streaming client with tool-use assembly.

The existing `core/anthropic_client.py` is REST-only: one request, one
response, no streaming, no tool-use. The Soniox stitched voice bridge needs
token-by-token streaming (for low-latency TTS) AND tool-use blocks (for
invoking patient/scheduling/insurance/billing tools).

This module adds `run_claude_turn()` — a focused helper that:
  - Opens an Anthropic streaming connection (SSE over HTTPS).
  - Emits each text delta via `on_text` (caller streams it into TTS).
  - Assembles partial tool_use JSON deltas into a complete input dict, then
    emits via `on_tool_use`.
  - Supports cancellation via an `asyncio.Event` (bridge sets it on barge-in).
  - Returns the full assistant message suitable for appending to the
    conversation history and feeding back to Claude with tool_result blocks.

Reference: Anthropic Messages API streaming protocol (SSE events:
`message_start`, `content_block_start`, `content_block_delta`,
`content_block_stop`, `message_delta`, `message_stop`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"          # a small, fast model: a lookup-and-answer persona does not need more


def get_api_key() -> Optional[str]:
    """The model key, from the environment."""
    return os.environ.get("ANTHROPIC_API_KEY") or None

logger = logging.getLogger(__name__)


@dataclass
class ToolUseCall:
    """A fully-assembled tool_use block emitted during a streamed turn."""
    id: str
    name: str
    input: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamedAssistantMessage:
    """The assistant's full message, in Anthropic `content` block format.

    `content` is a list of blocks (either `{"type":"text","text":...}` or
    `{"type":"tool_use","id":...,"name":...,"input":...}`) suitable for
    direct appending to `messages=[{"role":"assistant","content":...}]` on
    the next turn.
    """
    content: List[Dict[str, Any]] = field(default_factory=list)
    stop_reason: Optional[str] = None
    # Token counts the API reports, including cache_creation_input_tokens and cache_read_input_tokens: how much of the
    # prompt was served from the prompt cache (system prompt marked cache_control below) rather than read again.
    usage: Dict[str, int] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @property
    def tool_uses(self) -> List[ToolUseCall]:
        return [
            ToolUseCall(id=b["id"], name=b["name"], input=b.get("input", {}))
            for b in self.content if b.get("type") == "tool_use"
        ]


# Real latency fix (2026-10-01): every run_claude_turn() call used to open
# (and tear down) its own httpx.AsyncClient, paying a fresh TCP+TLS
# handshake to api.anthropic.com on every single call. A tool-calling turn
# like the public booking chat's can make several sequential calls per user
# message (MAX_TOOL_ROUNDTRIPS_PER_TURN) -- each one re-paying that
# handshake added real, compounding latency. A shared, process-wide client
# with keep-alive reuses the TLS connection across calls. `request_timeout`
# is still honored per-call via the `timeout=` kwarg on client.stream().
_shared_http_client: Optional[httpx.AsyncClient] = None
_shared_http_client_lock = asyncio.Lock()


async def _get_shared_http_client() -> httpx.AsyncClient:
    global _shared_http_client
    if _shared_http_client is None:
        async with _shared_http_client_lock:
            if _shared_http_client is None:
                _shared_http_client = httpx.AsyncClient(
                    limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
                )
    return _shared_http_client


async def run_claude_turn(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    system_prompt: str,
    on_text: Optional[Callable[[str], Awaitable[None]]] = None,
    on_tool_use: Optional[Callable[[ToolUseCall], Awaitable[None]]] = None,
    cancel_event: Optional[asyncio.Event] = None,
    model: Optional[str] = None,
    max_tokens: int = 1024,
    request_timeout: float = 60.0,
    temperature: Optional[float] = None,
) -> StreamedAssistantMessage:
    """Run one Claude turn with streaming text + streaming tool-use assembly.

    Args:
        messages: Anthropic `messages` array (role/content pairs so far).
        tools: Anthropic tool definitions (list of dicts from
               `ToolDefinition.to_anthropic_format()`).
        system_prompt: System prompt string.
        on_text: Optional async callback invoked with each text delta as it
                 arrives. Caller typically pipes this into the TTS stream.
        on_tool_use: Optional async callback invoked once per `tool_use`
                     block, AFTER all its partial_json deltas have been
                     assembled into a complete `input` dict.
        cancel_event: Optional asyncio.Event. When set, the stream is
                      abandoned cleanly (no further callbacks, connection
                      closes, whatever was already emitted remains).
        model: Override model id; defaults to FAKE_PAYER_MODEL env → DEFAULT_MODEL.
        max_tokens: Anthropic max_tokens for this turn.
        request_timeout: HTTP read timeout.
        temperature: 0.0-1.0; None sends nothing (the API default). A generator whose output people compare passes a low
            value (see core/anthropic_client.call_anthropic). Out of range raises ValueError.

    Returns:
        StreamedAssistantMessage with `content` (list of blocks) and
        `stop_reason`. On cancellation, content reflects what was emitted
        before cancel — usable for debugging or partial logging, but the
        caller should NOT append a canceled assistant turn to history.

    Raises:
        RuntimeError on missing API key, HTTP error, or malformed stream.
    """
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    effective_model = model or os.environ.get("FAKE_PAYER_MODEL", DEFAULT_MODEL)

    # Real latency fix (2026-10-01): system_prompt and tools are IDENTICAL
    # across every call in this turn's tool-calling loop, and barely change
    # turn-to-turn within one chat session -- a textbook case for Anthropic
    # prompt caching. Marking the system block with cache_control lets
    # Anthropic skip reprocessing the (tools + system) prefix on every call
    # after the first cache write, cutting real per-call latency for any
    # multi-round tool-calling loop (e.g. modules/communication/backend/
    # public_booking_chat.py's run_chat_turn). Below Anthropic's minimum
    # cacheable length, cache_control is simply a no-op -- safe to always set.
    payload: Dict[str, Any] = {
        "model": effective_model,
        "max_tokens": max_tokens,
        "stream": True,
        "system": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
        # Measured on this line (2026-11-12): with tools present the model reasons before it speaks by default, which took the time to the first word from 0.52 s to 1.34 s.
        # On a phone call that is dead air, and a lookup-and-answer persona does not need it.
        "thinking": {"type": "disabled"},
    }
    if tools:
        payload["tools"] = tools
    if temperature is not None:
        if not (0.0 <= temperature <= 1.0):
            raise ValueError(f"temperature must be between 0.0 and 1.0, got {temperature!r}")
        payload["temperature"] = temperature

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "prompt-caching-2024-07-31",
        "content-type": "application/json",
        "accept": "text/event-stream",
    }

    assistant = StreamedAssistantMessage()

    # Per-block accumulators. Keyed by content-block index from Anthropic.
    # text block: accumulate deltas in .text. tool_use: accumulate partial_json,
    # parse once on content_block_stop.
    block_state: Dict[int, Dict[str, Any]] = {}

    client = await _get_shared_http_client()
    async with client.stream(
        "POST",
        ANTHROPIC_API_URL,
        headers=headers,
        json=payload,
        timeout=request_timeout,
    ) as resp:
        if resp.status_code != 200:
            # Drain the body for the error message.
            body = (await resp.aread()).decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Anthropic streaming error: {resp.status_code} {body[:400]}"
            )

        event_name: Optional[str] = None
        async for raw_line in resp.aiter_lines():
            if cancel_event is not None and cancel_event.is_set():
                # Tear down early. The async context manager closes the
                # underlying connection on exit.
                logger.info("run_claude_turn: cancel_event set, aborting stream")
                return assistant

            if not raw_line:
                event_name = None
                continue

            # SSE framing: `event: <name>` then `data: <json>`.
            if raw_line.startswith("event:"):
                event_name = raw_line[len("event:"):].strip()
                continue
            if not raw_line.startswith("data:"):
                continue

            data_str = raw_line[len("data:"):].strip()
            if not data_str or data_str == "[DONE]":
                continue

            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                logger.debug("run_claude_turn: non-JSON SSE data dropped")
                continue

            etype = event_name or event.get("type")

            if etype == "message_start":
                for key, value in ((event.get("message") or {}).get("usage") or {}).items():
                    if isinstance(value, int):
                        assistant.usage[key] = value
            elif etype == "content_block_start":
                idx = event.get("index", 0)
                block = event.get("content_block") or {}
                btype = block.get("type")
                if btype == "text":
                    block_state[idx] = {"type": "text", "text": ""}
                elif btype == "tool_use":
                    block_state[idx] = {
                        "type": "tool_use",
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "partial_json": "",
                    }

            elif etype == "content_block_delta":
                idx = event.get("index", 0)
                delta = event.get("delta") or {}
                dtype = delta.get("type")
                bs = block_state.get(idx)
                if bs is None:
                    continue
                if dtype == "text_delta":
                    chunk = delta.get("text", "")
                    bs["text"] = bs.get("text", "") + chunk
                    if chunk and on_text is not None:
                        try:
                            await on_text(chunk)
                        except Exception as e:
                            logger.exception("on_text callback failed: %s", e)
                elif dtype == "input_json_delta":
                    bs["partial_json"] = bs.get("partial_json", "") + delta.get("partial_json", "")

            elif etype == "content_block_stop":
                idx = event.get("index", 0)
                bs = block_state.pop(idx, None)
                if bs is None:
                    continue
                if bs["type"] == "text":
                    assistant.content.append({"type": "text", "text": bs.get("text", "")})
                elif bs["type"] == "tool_use":
                    raw = bs.get("partial_json", "") or "{}"
                    try:
                        input_obj = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning(
                            "run_claude_turn: tool_use input JSON parse failed, "
                            "treating as empty: %r", raw[:200]
                        )
                        input_obj = {}
                    call = ToolUseCall(
                        id=bs.get("id") or "",
                        name=bs.get("name") or "",
                        input=input_obj,
                    )
                    assistant.content.append({
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.input,
                    })
                    if on_tool_use is not None:
                        try:
                            await on_tool_use(call)
                        except Exception as e:
                            logger.exception("on_tool_use callback failed: %s", e)

            elif etype == "message_delta":
                delta = event.get("delta") or {}
                if "stop_reason" in delta:
                    assistant.stop_reason = delta["stop_reason"]
                for key, value in (event.get("usage") or {}).items():
                    if isinstance(value, int):
                        assistant.usage[key] = value

            elif etype == "message_stop":
                # End of the turn; httpx's aiter_lines will end naturally.
                pass

            # Other event types (ping, error) — ignored for now except errors.
            elif etype == "error":
                err = event.get("error") or {}
                raise RuntimeError(
                    f"Anthropic stream error: {err.get('type')} {err.get('message')}"
                )

    return assistant
