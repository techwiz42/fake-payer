"""The answer response: one connect action, streaming linear PCM at 8 kHz, which is what Soniox speaks natively."""
from fake_payer.soniox_ncco import BRIDGE_PATH, CONTENT_TYPE, build_ncco, inbound_ws_url


def test_the_ncco_is_one_connect_action_with_the_pcm_8khz_content_type():
    ncco = build_ncco(inbound_ws_url("line.example.org", "call-uuid-1"))
    assert len(ncco) == 1 and ncco[0]["action"] == "connect"
    [endpoint] = ncco[0]["endpoint"]
    assert endpoint == {"type": "websocket", "uri": "wss://line.example.org/ws/call-uuid-1", "content-type": "audio/l16;rate=8000"}
    assert CONTENT_TYPE == "audio/l16;rate=8000" and BRIDGE_PATH == "/ws"


def test_there_is_no_talk_preamble_that_vonage_would_tear_down():
    assert [action["action"] for action in build_ncco("wss://x/ws/y")] == ["connect"]
