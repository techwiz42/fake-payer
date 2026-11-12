"""Configuration: refuses to start without its credentials, and never shows one."""
import logging

import pytest

from fake_payer.config import Config, ConfigError

GOOD = {"SONIOX_API_KEY": "soniox-secret-value-1", "ANTHROPIC_API_KEY": "model-secret-value-2", "FAKE_PAYER_NUMBER": "+12025550123"}


def test_reads_the_required_values_and_defaults_the_rest():
    config = Config.from_env(GOOD)
    assert config.number == "+12025550123"
    assert (config.max_concurrent_calls, config.max_calls_per_number_per_hour, config.max_call_seconds) == (3, 5, 300)
    assert config.daily_spend_ceiling_usd == 5.0 and config.log_retention_days == 7 and config.tts_voice == "Mark"


@pytest.mark.parametrize("name", sorted(GOOD))
def test_refuses_to_start_when_a_required_value_is_missing(name):
    environ = {k: v for k, v in GOOD.items() if k != name}
    with pytest.raises(ConfigError) as error:
        Config.from_env(environ)
    assert name in str(error.value)


def test_a_blank_value_counts_as_missing():
    with pytest.raises(ConfigError):
        Config.from_env({**GOOD, "SONIOX_API_KEY": "   "})


def test_limits_can_be_set_and_must_be_positive_numbers():
    config = Config.from_env({**GOOD, "MAX_CALL_SECONDS": "120", "DAILY_SPEND_CEILING_USD": "2.5", "TTS_VOICE": "Other"})
    assert config.max_call_seconds == 120 and config.daily_spend_ceiling_usd == 2.5 and config.tts_voice == "Other"
    for bad in ("0", "-3", "many"):
        with pytest.raises(ConfigError):
            Config.from_env({**GOOD, "MAX_CONCURRENT_CALLS": bad})


def test_no_credential_value_appears_in_text_form_logs_or_errors(caplog):
    config = Config.from_env(GOOD)
    secrets = (GOOD["SONIOX_API_KEY"], GOOD["ANTHROPIC_API_KEY"])
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("fake_payer").info("config: %s %r", config, config)
    shown = repr(config) + str(config) + caplog.text
    assert not any(secret in shown for secret in secrets)
    with pytest.raises(ConfigError) as error:
        Config.from_env({**GOOD, "MAX_CALL_SECONDS": GOOD["SONIOX_API_KEY"]})
    assert not any(secret in str(error.value) for secret in secrets)
