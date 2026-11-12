# Copied from agent_framework b227c23b: core/tests/test_anthropic_streaming_client.py
# Changes: import path; the line that put the platform's root on the import path is gone.
# Changes from the original are listed in this header; everything else is as it was.
"""
run_claude_turn tests -- real latency fix (2026-10-01).

Covers the three changes made to cut per-call latency in the public booking
chat's multi-round tool-calling loop (and, since this function is shared,
the live voice bridge too):
  1. system_prompt is sent as a cache_control-tagged content block, not a
     plain string, plus the anthropic-beta header -- Anthropic prompt
     caching, so repeated calls with the same system+tools don't get
     reprocessed from scratch.
  2. The underlying httpx client is a shared, module-level, keep-alive
     client reused across calls instead of a fresh one per call (TLS
     handshake elimination).
  3. request_timeout is still honored per-call despite the shared client.

No real network call is made -- httpx.MockTransport intercepts the request
and returns a synthetic Anthropic SSE stream.
"""
import json

import httpx
import pytest

import fake_payer.anthropic_streaming_client as streaming_client
from fake_payer.anthropic_streaming_client import run_claude_turn


def _sse_body(text: str = "Hello!", stop_reason: str = "end_turn") -> bytes:
    events = [
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    lines = []
    for event_name, data in events:
        lines.append(f"event: {event_name}")
        lines.append(f"data: {json.dumps(data)}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


@pytest.fixture(autouse=True)
def _reset_shared_client():
    """Each test gets a clean slate for the module-level shared client."""
    streaming_client._shared_http_client = None
    yield
    streaming_client._shared_http_client = None


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    monkeypatch.setattr(streaming_client, "get_api_key", lambda: "test-key")


@pytest.mark.asyncio
async def test_system_prompt_sent_with_cache_control_and_beta_header():
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, content=_sse_body(), headers={"content-type": "text/event-stream"})

    streaming_client._shared_http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await run_claude_turn(
        messages=[{"role": "user", "content": "hi"}], tools=[], system_prompt="You are a helpful assistant.",
    )

    assert result.text == "Hello!"
    assert result.stop_reason == "end_turn"
    assert len(captured_requests) == 1

    req = captured_requests[0]
    assert req.headers["anthropic-beta"] == "prompt-caching-2024-07-31"
    body = json.loads(req.content)
    assert body["system"] == [
        {"type": "text", "text": "You are a helpful assistant.", "cache_control": {"type": "ephemeral"}}
    ]


@pytest.mark.asyncio
async def test_shared_client_is_reused_across_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse_body(), headers={"content-type": "text/event-stream"})

    streaming_client._shared_http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client_before = streaming_client._shared_http_client

    await run_claude_turn(messages=[{"role": "user", "content": "hi"}], tools=[], system_prompt="System one.")
    await run_claude_turn(messages=[{"role": "user", "content": "hi again"}], tools=[], system_prompt="System one.")

    assert streaming_client._shared_http_client is client_before


@pytest.mark.asyncio
async def test_request_timeout_still_passed_through_per_call():
    captured_kwargs = {}
    real_stream = httpx.AsyncClient.stream

    def fake_stream(self, method, url, **kwargs):
        captured_kwargs.update(kwargs)
        return real_stream(self, method, url, **kwargs)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse_body(), headers={"content-type": "text/event-stream"})

    streaming_client._shared_http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    import unittest.mock as mock
    with mock.patch.object(httpx.AsyncClient, "stream", fake_stream):
        await run_claude_turn(
            messages=[{"role": "user", "content": "hi"}], tools=[], system_prompt="System.",
            request_timeout=12.5,
        )

    assert captured_kwargs.get("timeout") == 12.5


@pytest.mark.asyncio
async def test_tool_use_still_assembled_correctly():
    events = [
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
            "type": "tool_use", "id": "toolu_1", "name": "check_availability",
        }}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {
            "type": "input_json_delta", "partial_json": '{"location_id": "abc"}',
        }}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    lines = []
    for event_name, data in events:
        lines.append(f"event: {event_name}")
        lines.append(f"data: {json.dumps(data)}")
        lines.append("")
    body = ("\n".join(lines) + "\n").encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    streaming_client._shared_http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await run_claude_turn(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "check_availability", "input_schema": {}}],
        system_prompt="System.",
    )

    assert len(result.tool_uses) == 1
    assert result.tool_uses[0].name == "check_availability"
    assert result.tool_uses[0].input == {"location_id": "abc"}
