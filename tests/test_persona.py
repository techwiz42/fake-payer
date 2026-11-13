"""The opening line: names the fictional provider, says it is fictional and that the call is recorded, and never names a real insurer."""
import asyncio

from fake_payer.persona import DISCLOSURE, PROVIDER_NAME, build_system_prompt
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


def test_the_call_reference_is_eight_digits_stable_within_a_call_and_different_between_calls():
    from fake_payer.persona import call_reference
    first = call_reference("call-uuid-1")
    assert first.isdigit() and len(first) == 8
    assert call_reference("call-uuid-1") == first and call_reference("call-uuid-2") != first


def test_the_prompt_names_the_representative_gives_the_reference_and_forbids_ungrounded_facts():
    from fake_payer.persona import PROVIDER_NAME, REPRESENTATIVE_NAME, build_system_prompt, call_reference
    prompt = build_system_prompt("call-uuid-1")
    assert REPRESENTATIVE_NAME in prompt and PROVIDER_NAME in prompt and call_reference("call-uuid-1") in prompt
    assert "must come from a lookup_coverage result" in prompt and "Never recall, estimate, round, calculate or guess" in prompt
    assert "fictional" in prompt and call_reference("call-uuid-2") not in prompt


def test_the_prompt_names_no_real_insurer():
    from fake_payer.persona import build_system_prompt
    assert scan_text(build_system_prompt("x")) == []


def test_the_prompt_asks_for_the_practice_provider_and_patient_details_without_requiring_them():
    from fake_payer.persona import build_system_prompt
    prompt = build_system_prompt("call-uuid-1")
    for wanted in ("member number", "date of birth", "name", "group number", "practice's NPI", "provider's NPI", "last four digits of the practice's tax ID"):
        assert wanted in prompt, wanted
    assert "Do not insist and do not refuse to go on" in prompt and "still_needed" in prompt


def test_the_prompt_does_not_ask_for_a_procedure_code_to_answer_a_benefits_question():
    from fake_payer.persona import build_system_prompt
    prompt = build_system_prompt("call-uuid-1")
    assert "A procedure code is not needed" in prompt and "ask for the code" not in prompt.lower().replace("offer the code", "")


def test_it_is_told_to_look_everything_up_before_it_speaks_and_to_reply_in_one_go():
    prompt = build_system_prompt("call-1")
    assert "Make every tool call first, before you say anything" in prompt 


def test_it_does_not_ask_whether_there_is_anything_else_after_each_answer():
    prompt = build_system_prompt("call-1")
    assert 'Do not ask "Is there anything else I can help you with?"' in prompt and "never after each answer" in prompt
