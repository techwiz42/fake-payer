"""The call token: only a call that came through the answer request can open the audio websocket."""
from urllib.parse import parse_qs

from fake_payer.tokens import TOKEN_SECONDS, WITHHELD, CallTokens, key_matches


class Clock:
    def __init__(self): self.now = 1_000_000.0
    def __call__(self): return self.now


def tokens(clock=None):
    return CallTokens("a-long-answer-key-for-tests", clock or Clock())


def test_a_token_issued_for_a_call_verifies_for_that_call():
    t = tokens()
    caller = t.caller("+12025550100")
    query = parse_qs(t.issue("call-1", caller))
    assert t.verify("call-1", query) == caller


def test_a_token_for_another_call_or_altered_or_missing_is_refused():
    t = tokens()
    caller = t.caller("+12025550100")
    query = parse_qs(t.issue("call-1", caller))
    assert t.verify("call-2", query) is None
    assert t.verify("call-1", {**query, "l": ["9999"]}) is None             # the identity cannot be changed
    assert t.verify("call-1", {**query, "t": ["0" * 32]}) is None
    assert t.verify("call-1", {}) is None
    assert t.verify("call-1", {**query, "e": ["soon"]}) is None


def test_a_token_expires():
    clock = Clock()
    t = tokens(clock)
    query = parse_qs(t.issue("call-1", t.caller("+12025550100")))
    clock.now += TOKEN_SECONDS - 1
    assert t.verify("call-1", query) is not None
    clock.now += 2
    assert t.verify("call-1", query) is None


def test_a_token_made_with_another_key_is_refused():
    other = CallTokens("a-different-answer-key-xxxxxxxx", Clock())
    query = parse_qs(other.issue("call-1", other.caller("+12025550100")))
    assert tokens().verify("call-1", query) is None


def test_the_same_number_gives_the_same_key_and_the_number_is_not_in_it():
    t = tokens()
    a, b, c = t.caller("+1 (202) 555-0100"), t.caller("12025550100"), t.caller("+12025550101")
    assert a == b and a.key != c.key and a.last4 == "0100"
    assert "2025550100" not in a.key and "2025550100" not in t.issue("call-1", a)


def test_a_new_process_cannot_link_numbers_to_the_old_ones():
    assert tokens().caller("+12025550100").key != tokens().caller("+12025550100").key


def test_calls_with_no_number_share_one_identity():
    t = tokens()
    for number in (None, "", "anonymous", "Unknown", "restricted", "private"):
        assert t.caller(number).key == WITHHELD


def test_the_answer_key_comparison():
    assert key_matches("abc", "abc") and not key_matches("abd", "abc") and not key_matches("", "abc") and not key_matches(None, "abc")
