"""The lookup: the identity check, consistent answers, and each awkward case."""
import pytest

from fake_payer.coverage import Fixture, UNVERIFIED, make_tools


@pytest.fixture(scope="module")
def fixture():
    return Fixture.load()


def test_matching_details_verify_the_member_and_give_the_plan(fixture):
    result = fixture.lookup("M100001", "1985-03-14")
    assert result["verified"] and result["eligibility"] == {"status": "active", "effective_date": "2025-01-01"}
    assert result["plan"] == {"name": "Acme Gold Dental", "annual_maximum": 1500, "annual_maximum_remaining": 1500, "deductible": 50, "deductible_remaining": 50}


def test_the_identifier_is_not_case_sensitive(fixture):
    assert fixture.lookup(" m100001 ", "1985-03-14")["verified"]


def test_a_wrong_date_of_birth_an_unknown_member_and_blanks_all_get_the_same_answer(fixture):
    answers = [fixture.lookup("M100001", "1985-03-15"), fixture.lookup("M999999", "1985-03-14"), fixture.lookup("", ""), fixture.lookup(None, None)]
    assert all(a == UNVERIFIED for a in answers)
    assert not any(set(a) - {"verified", "message"} for a in answers)          # nothing but "could not be verified": no member, plan or eligibility leaks


def test_the_same_question_twice_gives_the_same_answer(fixture):
    assert fixture.lookup("M100001", "1985-03-14", "D0120") == fixture.lookup("M100001", "1985-03-14", "D0120")


def test_a_procedure_gives_its_percentage_deductible_rule_and_frequency(fixture):
    procedure = fixture.lookup("M100001", "1985-03-14", "d2391")["procedure"]
    assert procedure["found"] and procedure["coverage_percent"] == 80 and procedure["deductible_applies"] is True
    assert procedure["frequency_limit"] is None and procedure["covered_now"] is True
    preventive = fixture.lookup("M100001", "1985-03-14", "D0120")["procedure"]
    assert preventive["coverage_percent"] == 100 and preventive["deductible_applies"] is False
    assert preventive["frequency_limit"] == {"times": 2, "period": "calendar year", "times_used": 0, "times_remaining": 2}


def test_a_used_up_frequency_limit_means_not_covered_now_and_says_why(fixture):
    procedure = fixture.lookup("M100002", "1990-07-02", "D1110")["procedure"]
    assert procedure["covered_now"] is False and procedure["reason"] == "the frequency limit has been reached"
    assert procedure["frequency_limit"]["times_remaining"] == 0
    assert fixture.lookup("M100002", "1990-07-02", "D1206")["procedure"]["covered_now"] is True       # a different code is unaffected


def test_a_terminated_member_gets_the_end_date_and_no_coverage_figures(fixture):
    result = fixture.lookup("M100003", "1978-11-30", "D0120")
    assert result["verified"] and result["eligibility"]["status"] == "terminated" and result["eligibility"]["termination_date"] == "2026-06-30"
    assert result["coverage_available"] is False and "plan" not in result and "procedure" not in result


def test_the_remaining_annual_maximum_is_reported(fixture):
    plan = fixture.lookup("M100004", "2001-01-20")["plan"]
    assert (plan["annual_maximum"], plan["annual_maximum_remaining"], plan["deductible_remaining"]) == (1000, 120, 0)


def test_a_reached_annual_maximum_means_not_covered_now(tmp_path):
    import yaml
    from fake_payer.coverage import FIXTURE_PATH
    data = yaml.safe_load(FIXTURE_PATH.read_text())
    data["members"]["M100004"]["annual_maximum_used"] = 1000
    path = tmp_path / "f.yaml"
    path.write_text(yaml.safe_dump(data))
    procedure = Fixture.load(path).lookup("M100004", "2001-01-20", "D2740")["procedure"]
    assert procedure["covered_now"] is False and procedure["reason"] == "the annual maximum has been reached"


def test_a_code_the_fixture_does_not_hold_is_not_found_and_not_guessed(fixture):
    assert fixture.lookup("M100001", "1985-03-14", "D9999")["procedure"] == {"code": "D9999", "found": False}


async def test_the_tool_the_bridge_runs_is_the_lookup(fixture):
    definition, function = make_tools(fixture)["lookup_coverage"]
    assert definition["name"] == "lookup_coverage" and definition["input_schema"]["required"] == ["member_id", "date_of_birth"]
    assert await function({"member_id": "M100001", "date_of_birth": "1985-03-14", "procedure_code": "D0120"}) == fixture.lookup("M100001", "1985-03-14", "D0120")
    assert await function({}) == UNVERIFIED
