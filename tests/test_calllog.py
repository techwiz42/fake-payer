"""The call log and the command that reads it."""
import json
import os
import stat
from types import SimpleNamespace

from fake_payer import calls
from fake_payer.calllog import CallLog, ended_by, mask, turns_from_transcript


class Clock:
    def __init__(self): self.now = 1_700_000_000.0
    def __call__(self): return self.now


TURNS = [
    {"role": "assistant", "text": "I am Dana."},
    {"role": "tool_result", "content": {"procedure": {"coverage_percent": 80}}},
    {"role": "assistant", "text": "That is covered at eighty percent."},
]


def test_a_call_is_one_complete_entry_with_the_number_masked(tmp_path):
    log = CallLog(str(tmp_path), Clock())
    path = log.write(reference="48291703", last4="0100", ended="caller_hung_up", duration_seconds=41.234, turns=TURNS)
    record = json.loads(path.read_text())
    assert record["reference"] == "48291703" and record["caller"] == "***0100" and record["ended"] == "caller_hung_up" and record["duration_seconds"] == 41.2
    assert record["time"].startswith("2023-11-14") and record["turns"] == TURNS
    assert record["stated_figures"] == ["80 percent"] and record["ungrounded_figures"] == []


def test_a_figure_no_lookup_returned_is_flagged_in_the_entry(tmp_path):
    log = CallLog(str(tmp_path), Clock())
    log.write(reference="11111111", last4="0100", ended="caller_hung_up", turns=TURNS + [{"role": "assistant", "text": "And a crown is fifty percent."}])
    assert log.get("11111111")["ungrounded_figures"] == ["3: 50 percent"]


def test_masking():
    assert mask("0100") == "***0100" and mask("none") == "withheld" and mask("") == "withheld"


def test_files_are_private_to_the_user(tmp_path):
    path = CallLog(str(tmp_path / "calls"), Clock()).write(reference="22222222", last4="0100", ended="caller_hung_up")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_a_call_is_found_by_its_reference_and_recent_calls_list_newest_first(tmp_path):
    clock = Clock()
    log = CallLog(str(tmp_path), clock)
    log.write(reference="00000001", last4="0100", ended="caller_hung_up")
    clock.now += 60
    log.write(reference="00000002", last4="none", ended="refused_busy")
    assert log.get("00000001")["caller"] == "***0100" and log.get("99999999") is None
    assert [c["reference"] for c in log.recent()] == ["00000002", "00000001"] and log.recent()[0]["caller"] == "withheld"


def test_entries_older_than_the_retention_period_are_removed_and_recent_ones_kept(tmp_path):
    clock = Clock()
    log = CallLog(str(tmp_path), clock)
    log.write(reference="00000001", last4="0100", ended="caller_hung_up")
    clock.now += 8 * 86400
    log.write(reference="00000002", last4="0100", ended="caller_hung_up")
    assert log.cleanup(7) == 1
    assert log.get("00000001") is None and log.get("00000002") is not None


def test_the_bridges_turn_records_become_log_turns():
    transcript = [SimpleNamespace(role="assistant", text="hello", tool_uses=[]), SimpleNamespace(role="user", text="hi"),
                  SimpleNamespace(role="tool_result", text='{"x": 1}'), SimpleNamespace(role="tool_result", text="not json")]
    assert turns_from_transcript(transcript) == [{"role": "assistant", "text": "hello"}, {"role": "user", "text": "hi"},
                                                 {"role": "tool_result", "content": {"x": 1}}, {"role": "tool_result", "content": "not json"}]


def test_how_a_call_ended():
    assert (ended_by("watchdog"), ended_by("error"), ended_by("caller"), ended_by(None)) == ("time_limit", "error", "caller_hung_up", "caller_hung_up")


def test_the_command_lists_shows_and_cleans_up(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CALL_LOG_DIR", str(tmp_path / "log"))
    monkeypatch.setenv("LOG_RETENTION_DAYS", "7")
    log = CallLog(str(tmp_path / "log"))
    log.write(reference="12345678", last4="0100", ended="caller_hung_up", turns=TURNS)
    assert calls.main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "12345678" in listed and "***0100" in listed and "ungrounded=0" in listed
    assert calls.main(["show", "12345678"]) == 0
    assert json.loads(capsys.readouterr().out)["turns"] == TURNS
    assert calls.main(["show", "00000000"]) == 1
    assert calls.main(["cleanup"]) == 0 and "removed 0" in capsys.readouterr().out
