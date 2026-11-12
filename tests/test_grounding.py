"""The grounding check: figures with a unit are compared with the tool results; unitless numbers are not coverage facts."""
from fake_payer.grounding import Figure, check_turns, numbers_in, spoken_figures, unsupported_figures


def figs(text):
    return [(f.value, f.unit) for f in spoken_figures(text)]


def test_digits_and_number_words_with_a_unit_are_read():
    assert figs("It pays 80 percent.") == [(80.0, "percent")]
    assert figs("It pays eighty percent.") == [(80.0, "percent")]
    assert figs("That is 80% covered.") == [(80.0, "percent")]
    assert figs("You have $1,500 a year.") == [(1500.0, "dollars")]
    assert figs("Fifteen hundred dollars a year.") == [(1500.0, "dollars")]
    assert figs("One thousand three hundred and twenty dollars is left.") == [(1320.0, "dollars")]
    assert figs("Thirteen hundred twenty dollars") == [(1320.0, "dollars")]
    assert figs("Two times a calendar year, so twice.") == [(2.0, "times"), (2.0, "times")]
    assert figs("fifty five dollars") == [(55.0, "dollars")]


def test_numbers_without_a_unit_are_not_coverage_facts():
    assert figs("One moment please. Your member number is M100001 and the reference is 48291703.") == []
    assert figs("The plan year started on January 1.") == []


def test_numbers_in_a_tool_result_include_those_inside_strings():
    assert numbers_in({"a": 80, "b": {"c": [1.5, "2 per calendar year"]}, "d": True, "e": None}) == {80.0, 1.5, 2.0}


def test_a_supported_figure_passes_and_an_invented_one_is_reported():
    result = {"procedure": {"coverage_percent": 80}, "plan": {"annual_maximum_remaining": 1320}}
    assert unsupported_figures("Eighty percent, and you have $1,320 left.", [result]) == []
    bad = unsupported_figures("Ninety percent, and you have $1,320 left.", [result])
    assert bad == [Figure(90.0, "percent")] and str(bad[0]) == "90 percent"


def test_tool_results_given_as_json_strings_are_read():
    assert unsupported_figures("eighty percent", ['{"coverage_percent": 80}']) == []


def test_a_figure_stated_before_any_lookup_is_a_breach():
    turns = [{"role": "assistant", "text": "That is covered at eighty percent."}, {"role": "tool_result", "content": {"coverage_percent": 80}}]
    assert check_turns(turns) == [(0, Figure(80.0, "percent"))]


def test_a_later_turn_may_use_a_result_from_earlier_in_the_call():
    turns = [{"role": "tool_result", "content": {"coverage_percent": 80}}, {"role": "assistant", "text": "Eighty percent."}, {"role": "assistant", "text": "As I said, eighty percent."}]
    assert check_turns(turns) == []


def test_a_number_that_cannot_be_read_before_a_unit_fails_loudly_instead_of_being_skipped():
    for phrase in ("Thirteen twenty dollars", "twenty thirty percent", "five five percent", "hundred hundred dollars"):
        flagged = unsupported_figures(phrase, [{"x": 1320, "y": 5, "z": 20, "w": 30, "v": 100}])
        assert len(flagged) == 1 and str(flagged[0]).startswith("an unreadable figure"), phrase
