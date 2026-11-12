"""Replay of transcripts through the grounding check: a call whose representative states only what the lookup returned passes; one that invents a figure fails.

These transcripts are scripted in the call log's turn shape. Once real calls are logged, their transcripts are replayed the same way (see `check_turns`).
"""
import json
from pathlib import Path

import pytest

from fake_payer.grounding import check_turns

TRANSCRIPTS = sorted(Path(__file__).parent.joinpath("transcripts").glob("*.json"))


def test_there_are_transcripts_of_both_kinds():
    names = [p.name for p in TRANSCRIPTS]
    assert any(n.startswith("good_") for n in names) and any(n.startswith("bad_") for n in names)


@pytest.mark.parametrize("path", [p for p in TRANSCRIPTS if p.name.startswith("good_")], ids=lambda p: p.stem)
def test_a_grounded_call_has_no_breaches(path):
    assert check_turns(json.loads(path.read_text())["turns"]) == []


@pytest.mark.parametrize("path", [p for p in TRANSCRIPTS if p.name.startswith("bad_")], ids=lambda p: p.stem)
def test_an_invented_figure_is_caught(path):
    breaches = check_turns(json.loads(path.read_text())["turns"])
    assert breaches and [str(figure) for _, figure in breaches] == ["90 percent"]       # the percentage was invented; the deductible of fifty dollars was returned
