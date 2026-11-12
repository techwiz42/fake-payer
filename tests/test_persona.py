"""The opening line: names the fictional provider, says it is fictional and that the call is recorded, and never names a real insurer."""
import asyncio

from fake_payer.persona import DISCLOSURE, PROVIDER_NAME
from fake_payer.real_data_check import scan_text


def test_the_disclosure_names_the_provider_and_says_fictional_and_recorded():
    text = DISCLOSURE.lower()
    assert PROVIDER_NAME in DISCLOSURE and "fictional" in text and "recorded" in text and "transcribed" in text


def test_the_provider_name_is_not_a_real_insurers():
    assert scan_text(PROVIDER_NAME + " " + DISCLOSURE) == []


def test_the_bridge_begins_every_call_with_the_disclosure(monkeypatch):
    from tests.test_bridge import END, FakeCall, FakeSTT, FakeTTS
    from fake_payer import bridge as bridge_module
    from fake_payer.bridge import BridgeSettings, VoiceBridge

    made = {}
    monkeypatch.setattr(bridge_module, "SonioxSTTClient", lambda **kw: made.setdefault("stt", FakeSTT(**kw)))
    monkeypatch.setattr(bridge_module, "SonioxTTSClient", lambda **kw: made.setdefault("tts", FakeTTS(**kw)))

    async def run():
        call = FakeCall()
        bridge = VoiceBridge(BridgeSettings(call_uuid="c", greeting=DISCLOSURE, soniox_api_key="k"), {}, "persona")
        task = asyncio.create_task(bridge.run(call))
        for _ in range(200):
            if "tts" in made and made["tts"].synthesized:
                break
            await asyncio.sleep(0.01)
        call.incoming.put_nowait(END)
        await task
        return bridge

    bridge = asyncio.run(run())
    assert made["tts"].synthesized == [DISCLOSURE] and bridge.transcript[0].text == DISCLOSURE
