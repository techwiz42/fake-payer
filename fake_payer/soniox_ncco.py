# Copied from agent_framework b227c23b: modules/communication/backend/soniox_ncco.py
# Changes: the outbound URL, the "can Soniox run at all" gate (it read the platform's communication config; this program's own configuration refuses to start
# without the key) and the platform's route path are removed; the websocket path is this program's own, and its address may carry a signed token (tokens.py).
"""
The Vonage NCCO that connects an inbound call to the voice bridge.

Vonage delivers PCM16 at 8 kHz over the WebSocket; Soniox STT/TTS both speak that natively, so there is no resampling on either leg. The call's Vonage UUID is known
when the answer request arrives, so it goes in the websocket URL.
"""
from typing import Any, Dict, List

CONTENT_TYPE = "audio/l16;rate=8000"

BRIDGE_PATH = "/ws"


def inbound_ws_url(host: str, call_uuid: str, query: str = "") -> str:
    return f"wss://{host}{BRIDGE_PATH}/{call_uuid}" + (f"?{query}" if query else "")


def build_ncco(ws_url: str) -> List[Dict[str, Any]]:
    """A single ``connect`` action, no preamble. An earlier revision had a ``talk`` preamble with ``bargeIn: true``,
    which Vonage's NCCO validator tore down immediately (bargeIn requires a following ``input`` action, not
    ``connect``) -- a 0-duration call and a busy signal on the handset."""
    return [
        {
            "action": "connect",
            "endpoint": [{"type": "websocket", "uri": ws_url, "content-type": CONTENT_TYPE}],
        },
    ]
