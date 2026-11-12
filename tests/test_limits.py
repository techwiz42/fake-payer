"""Limits on volume and spend: simultaneous calls, calls per number per hour, the daily spend ceiling."""
from fake_payer.config import Config
from fake_payer.limits import HOUR, Limits

BASE = {"SONIOX_API_KEY": "k", "ANTHROPIC_API_KEY": "m", "FAKE_PAYER_NUMBER": "+12025550123", "ANSWER_KEY": "test-answer-key-0123456789"}


class Clock:
    def __init__(self): self.now = 1_700_000_000.0
    def __call__(self): return self.now


def limits(clock=None, **env):
    return Limits(Config.from_env({**BASE, **env}), clock or Clock())


def test_calls_up_to_the_simultaneous_cap_are_let_in_and_the_next_is_busy():
    l = limits(MAX_CONCURRENT_CALLS="2")
    a, b = l.admit("one"), l.admit("two")
    assert a[0].allowed and b[0].allowed
    refused, handle = l.admit("three")
    assert (refused.allowed, refused.reason, handle) == (False, "busy", None)


def test_ending_a_call_frees_its_place():
    l = limits(MAX_CONCURRENT_CALLS="1")
    _, handle = l.admit("one")
    assert l.admit("two")[0].reason == "busy"
    l.release(handle)
    assert l.admit("two")[0].allowed


def test_a_number_over_its_hourly_count_is_refused_until_the_hour_passes():
    clock = Clock()
    l = limits(clock, MAX_CALLS_PER_NUMBER_PER_HOUR="2", MAX_CONCURRENT_CALLS="10")
    for _ in range(2):
        decision, handle = l.admit("same")
        assert decision.allowed
        l.release(handle)
    assert l.admit("same")[0].reason == "rate"
    other, handle = l.admit("other")
    assert other.allowed                                     # another number is unaffected
    l.release(handle)
    clock.now += HOUR + 1
    assert l.admit("same")[0].allowed


def test_a_refused_attempt_does_not_lengthen_the_wait():
    clock = Clock()
    l = limits(clock, MAX_CALLS_PER_NUMBER_PER_HOUR="1", MAX_CONCURRENT_CALLS="10")
    l.release(l.admit("same")[1])
    for _ in range(5):
        clock.now += 600
        assert l.admit("same")[0].reason == "rate"           # retrying every ten minutes does not push the hour back
    clock.now += 600 + 1
    assert l.admit("same")[0].allowed


def test_calls_with_no_number_share_one_count():
    l = limits(MAX_CALLS_PER_NUMBER_PER_HOUR="1", MAX_CONCURRENT_CALLS="10")
    l.release(l.admit("withheld")[1])
    assert l.admit("withheld")[0].reason == "rate"


def test_finished_calls_count_toward_the_daily_ceiling_and_it_refuses_when_reached():
    clock = Clock()
    l = limits(clock, DAILY_SPEND_CEILING_USD="1", COST_PER_MINUTE_USD="0.5", MAX_CONCURRENT_CALLS="10", MAX_CALLS_PER_NUMBER_PER_HOUR="10")
    _, handle = l.admit("a")
    clock.now += 120                                           # two minutes at 0.50 a minute: the whole ceiling
    l.release(handle)
    assert abs(l.spend_today() - 1.0) < 1e-9
    assert l.admit("b")[0].reason == "spend"


def test_calls_in_progress_count_too_so_one_long_call_cannot_run_past_the_ceiling_unseen():
    clock = Clock()
    l = limits(clock, DAILY_SPEND_CEILING_USD="1", COST_PER_MINUTE_USD="0.5", MAX_CONCURRENT_CALLS="10")
    l.admit("a")
    clock.now += 120
    assert l.admit("b")[0].reason == "spend"


def test_the_ceiling_resets_the_next_day():
    clock = Clock()
    l = limits(clock, DAILY_SPEND_CEILING_USD="1", COST_PER_MINUTE_USD="1", MAX_CONCURRENT_CALLS="10", MAX_CALLS_PER_NUMBER_PER_HOUR="10")
    _, handle = l.admit("a")
    clock.now += 120
    l.release(handle)
    assert l.admit("b")[0].reason == "spend"
    clock.now += 24 * 3600
    assert l.admit("b")[0].allowed


def test_spend_is_checked_before_busy_before_rate():
    clock = Clock()
    l = limits(clock, DAILY_SPEND_CEILING_USD="1", COST_PER_MINUTE_USD="60", MAX_CONCURRENT_CALLS="1", MAX_CALLS_PER_NUMBER_PER_HOUR="1")
    l.admit("a")
    assert l.admit("a")[0].reason == "busy"                  # every limit is hit, but the call is not over the ceiling yet: busy comes first of the others
    clock.now += 2                                           # two seconds at 60 a minute: over the ceiling
    assert l.admit("a")[0].reason == "spend"
