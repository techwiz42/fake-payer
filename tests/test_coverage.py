"""The lookup: it works from whatever the caller gives, requires none of the optional details, and needs no procedure code."""
import pytest
import yaml

from fake_payer.coverage import ASKED_FOR, FIXTURE_PATH, Fixture, make_tools


@pytest.fixture(scope="module")
def fixture():
    return Fixture.load()


# ---- identifying the member ----------------------------------------------------

def test_a_member_number_and_date_of_birth_verify_the_member_and_give_the_plan_and_benefits(fixture):
    result = fixture.lookup(member_id="M100001", date_of_birth="1985-03-14")
    assert result["verified"] and result["eligibility"] == {"status": "active", "effective_date": "2025-01-01"}
    assert result["plan"] == {"name": "Acme Gold Dental", "annual_maximum": 1500, "deductible": 50, "family_deductible": 150, "orthodontic_lifetime_maximum": 1500, "orthodontic_age_limit": 65, "orthodontic_lifetime_maximum_used": 0,
                              "orthodontic_lifetime_maximum_remaining": 1500,
                              "annual_maximum_remaining": 1500, "deductible_remaining": 50}
    assert list(result["benefits"]) == ["preventive", "basic", "major", "orthodontics"]


def test_the_member_number_is_compared_the_way_a_payer_does(fixture):
    for spoken in (" m100001 ", "M-100 001", "m.100.001"):
        assert fixture.lookup(member_id=spoken, date_of_birth="1985-03-14")["verified"], spoken


def test_a_name_and_date_of_birth_are_enough_without_a_member_number(fixture):
    for name in ("Pat Sample", "sample, pat", "PAT  SAMPLE"):
        assert fixture.lookup(patient_name=name, date_of_birth="1985-03-14")["verified"], name
    assert fixture.lookup(patient_name="Pat Sample", date_of_birth="1985-03-15")["member"]["last_name"] == "Sample"      # another Pat Sample: a different, ordinary member


def test_the_date_of_birth_is_always_needed_and_what_else_was_given_must_agree(fixture):
    assert not fixture.lookup(member_id="M100001")["verified"]                                       # no date of birth
    assert not fixture.lookup(patient_name="Pat Sample")["verified"]
    assert not fixture.lookup(member_id="M100001", date_of_birth="1985-03-14", patient_name="Robin Example")["verified"]      # another person's name
    assert not fixture.lookup(member_id="M100001", date_of_birth="1985-03-14", group_number="G20002")["verified"]             # another group
    assert fixture.lookup(member_id="M100001", date_of_birth="1985-03-14", patient_name="Pat Sample", group_number="g10001")["verified"]


def test_a_wrong_detail_on_a_known_member_number_and_blanks_get_the_same_answer_and_nothing_leaks(fixture):
    same_input = [fixture.lookup(member_id="M100001", date_of_birth="1985-03-15"), fixture.lookup(member_id="M100001", date_of_birth="1985-03-16")]
    assert same_input[0] == same_input[1]                                                           # which detail was wrong is never said
    for answer in same_input + [fixture.lookup(), fixture.lookup(member_id=None, date_of_birth=None), fixture.lookup(member_id="M999999"), fixture.lookup(patient_name="Ann Lee")]:
        assert not answer["verified"] and set(answer) <= {"verified", "message", "still_needed", "required_to_continue", "standing_answers"}


def test_any_patient_who_is_named_with_a_date_of_birth_is_an_ordinary_active_member(fixture):
    for given in ({"member_id": "X-42", "date_of_birth": "1972-05-09"}, {"patient_name": "Ann Lee", "date_of_birth": "1972-05-09"},
                  {"member_id": "ZZ1", "patient_name": "Ann Lee", "date_of_birth": "1972-05-09", "group_number": "NOT-A-GROUP"}):
        result = fixture.lookup(**given)
        assert result["verified"] and result["eligibility"]["status"] == "active" and result["plan"]["annual_maximum_remaining"] == result["plan"]["annual_maximum"], given
        assert result == fixture.lookup(**given)                                                    # the same answer every time
    assert not fixture.lookup(member_id="X-42", date_of_birth="not a date")["verified"]
    assert fixture.lookup(patient_name="Ann Lee", date_of_birth="1972-05-09", group_number="G10001")["plan"]["name"] == "Acme Gold Dental"


def test_the_same_question_twice_gives_the_same_answer(fixture):
    assert fixture.lookup(member_id="M100001", date_of_birth="1985-03-14", procedure_code="D0120") == fixture.lookup(member_id="M100001", date_of_birth="1985-03-14", procedure_code="D0120")


# ---- asked for, never required -------------------------------------------------

def test_what_has_not_been_given_is_listed_and_none_of_it_is_required(fixture):
    nothing = fixture.lookup()
    assert nothing["still_needed"] == list(ASKED_FOR) and nothing["required_to_continue"] is False
    partly = fixture.lookup(member_id="M100001", date_of_birth="1985-03-14")
    assert partly["verified"] and partly["still_needed"] == ["patient_name", "group_number", "practice_npi", "provider_npi", "tin_last4"]
    full = fixture.lookup(member_id="M100001", date_of_birth="1985-03-14", patient_name="Pat Sample", group_number="G10001",
                          practice_npi="1234567893", provider_npi="1234567893", tin_last4="1234")
    assert full["verified"] and full["still_needed"] == []


def test_the_practice_and_provider_identifiers_are_accepted_but_do_not_decide_anything(fixture):
    base = fixture.lookup(member_id="M100001", date_of_birth="1985-03-14")
    with_ids = fixture.lookup(member_id="M100001", date_of_birth="1985-03-14", practice_npi="1234567893", provider_npi="9876543210", tin_last4="4321")
    assert {k: v for k, v in with_ids.items() if k != "still_needed"} == {k: v for k, v in base.items() if k != "still_needed"}


def test_an_identifier_of_the_wrong_shape_is_noted_but_does_not_stop_the_lookup(fixture):
    result = fixture.lookup(member_id="M100001", date_of_birth="1985-03-14", practice_npi="12345", tin_last4="12")
    assert result["verified"] and result["notes"] == ["practice_npi should be 10 digits", "tin_last4 should be 4 digits"]
    assert "practice_npi" not in result["still_needed"]                                              # it was given, even if wrong


# ---- benefits, with no procedure code ------------------------------------------

def test_the_benefits_are_given_by_category_with_the_services_and_limits_in_each(fixture):
    benefits = fixture.lookup(member_id="M100001", date_of_birth="1985-03-14")["benefits"]
    preventive = benefits["preventive"]
    assert preventive["covered"] and preventive["coverage_percent"] == 100 and preventive["deductible_applies"] is False and preventive["waiting_period_months"] == 0
    exam = next(s for s in preventive["services"] if s["code"] == "D0120")
    assert exam == {"code": "D0120", "description": "routine exam", "frequency_limit": {"times": 2, "period": "calendar year"}, "times_used": 0, "times_remaining": 2}
    assert benefits["basic"]["coverage_percent"] == 80 and benefits["basic"]["waiting_period_months"] == 6 and benefits["basic"]["deductible_applies"] is True
    assert benefits["major"]["coverage_percent"] == 50 and benefits["orthodontics"]["coverage_percent"] == 50


def test_used_up_limits_show_in_the_benefits_for_that_member(fixture):
    cleaning = next(s for s in fixture.lookup(member_id="M100002", date_of_birth="1990-07-02")["benefits"]["preventive"]["services"] if s["code"] == "D1110")
    assert cleaning["times_used"] == 2 and cleaning["times_remaining"] == 0


def test_a_plan_without_orthodontics_says_it_has_no_such_benefit(fixture):
    result = fixture.lookup(member_id="M100004", date_of_birth="2001-01-20")
    assert result["benefits"]["orthodontics"] == {"covered": False} and "orthodontic_lifetime_maximum" not in result["plan"]
    braces = fixture.lookup(member_id="M100004", date_of_birth="2001-01-20", procedure_code="D8080")["procedure"]
    assert braces["covered_now"] is False and braces["reason"] == "the plan has no benefit for this category"


def test_a_group_number_alone_gives_the_plans_benefits_but_nothing_about_a_member(fixture):
    result = fixture.lookup(group_number=" g-10001 ")
    assert result["verified"] is False and result["plan_level_only"] is True and result["plan"]["name"] == "Acme Gold Dental"
    assert "annual_maximum_remaining" not in result["plan"] and "deductible_remaining" not in result["plan"]
    assert "times_used" not in str(result["benefits"]) and "member" not in result and "eligibility" not in result
    assert "plan_level_only" not in fixture.lookup(group_number="G99999")                             # an unknown group gives nothing


# ---- a specific procedure, when a code is given --------------------------------

def test_a_procedure_gives_its_percentage_deductible_rule_and_frequency(fixture):
    procedure = fixture.lookup(member_id="M100001", date_of_birth="1985-03-14", procedure_code="d2391")["procedure"]
    assert procedure["found"] and procedure["coverage_percent"] == 80 and procedure["deductible_applies"] is True
    assert procedure["frequency_limit"] is None and procedure["covered_now"] is True
    preventive = fixture.lookup(member_id="M100001", date_of_birth="1985-03-14", procedure_code="D0120")["procedure"]
    assert preventive["coverage_percent"] == 100 and preventive["deductible_applies"] is False
    assert preventive["frequency_limit"] == {"times": 2, "period": "calendar year", "times_used": 0, "times_remaining": 2}


def test_a_used_up_frequency_limit_means_not_covered_now_and_says_why(fixture):
    procedure = fixture.lookup(member_id="M100002", date_of_birth="1990-07-02", procedure_code="D1110")["procedure"]
    assert procedure["covered_now"] is False and procedure["reason"] == "the frequency limit has been reached"
    assert fixture.lookup(member_id="M100002", date_of_birth="1990-07-02", procedure_code="D1206")["procedure"]["covered_now"] is True


def test_a_terminated_member_gets_the_end_date_and_no_coverage_figures(fixture):
    result = fixture.lookup(member_id="M100003", date_of_birth="1978-11-30", procedure_code="D0120")
    assert result["verified"] and result["eligibility"]["status"] == "terminated" and result["eligibility"]["termination_date"] == "2026-06-30"
    assert result["coverage_available"] is False and "plan" not in result and "procedure" not in result and "benefits" not in result


def test_the_remaining_annual_maximum_is_reported(fixture):
    plan = fixture.lookup(member_id="M100004", date_of_birth="2001-01-20")["plan"]
    assert (plan["annual_maximum"], plan["annual_maximum_remaining"], plan["deductible_remaining"]) == (1000, 120, 0)


def test_a_reached_annual_maximum_means_not_covered_now(tmp_path):
    data = yaml.safe_load(FIXTURE_PATH.read_text())
    data["members"]["M100004"]["annual_maximum_used"] = 1000
    path = tmp_path / "f.yaml"
    path.write_text(yaml.safe_dump(data))
    procedure = Fixture.load(path).lookup(member_id="M100004", date_of_birth="2001-01-20", procedure_code="D2740")["procedure"]
    assert procedure["covered_now"] is False and procedure["reason"] == "the annual maximum has been reached"


def test_a_code_the_fixture_does_not_hold_is_not_found_and_not_guessed(fixture):
    assert fixture.lookup(member_id="M100001", date_of_birth="1985-03-14", procedure_code="D9999")["procedure"] == {"code": "D9999", "found": False}


# ---- the tool the bridge runs --------------------------------------------------

async def test_the_tool_takes_every_field_as_optional_and_is_the_lookup(fixture):
    definition, function = make_tools(fixture)["lookup_coverage"]
    assert definition["name"] == "lookup_coverage" and definition["input_schema"]["required"] == []
    assert set(definition["input_schema"]["properties"]) == set(ASKED_FOR) | {"procedure_code"}
    arguments = {"member_id": "M100001", "date_of_birth": "1985-03-14", "procedure_code": "D0120", "group_number": "G10001"}
    assert await function(arguments) == fixture.lookup(**arguments)
    assert await function({}) == fixture.lookup()
    assert await function({"unexpected": "ignored"}) == fixture.lookup()


def test_the_standing_answers_come_back_with_every_lookup_even_before_a_patient_is_found(fixture):
    expected = {"employee_id": "3141", "fee_schedule": "The fee schedule that applies is dated 2026.", "covered_once_per_lifetime": "Yes.", "missing_tooth_clause": "The missing tooth clause applies to both orthodontics and general dentistry: the plan covers up to two missing teeth.",
                "paid_by_prior_carrier": "A prior carrier has paid nothing toward orthodontics: zero dollars.", "treatment_in_progress_how_paid": "There is no treatment already in progress."}
    assert fixture.lookup()["standing_answers"] == expected
    assert fixture.lookup(member_id="M100001", date_of_birth="1985-03-14")["standing_answers"] == expected


# ---- the form's questions ---------------------------------------------------------------------------------------------------

def test_every_question_of_a_practices_verification_form_has_an_answer(fixture):
    import importlib.util
    path = "/Users/bartelby/af-payer-call/modules/insurance/backend/sub_services/benefit_catalog_data.py"
    if not __import__("os").path.exists(path):
        pytest.skip("the platform's catalog is not alongside")
    spec = importlib.util.spec_from_file_location("catalog", path)
    catalog = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(catalog)
    asked = {e["question"] for e in fixture.benefit_answers}
    missing = [row["key"] for row in catalog.CATALOG if row["group"] != "office" and row["label"] not in asked]
    assert not missing, missing


@pytest.mark.parametrize("question, expected", [
    ("What is the waiting period for orthodontics?", "waiting period for orthodontics is twelve months"),
    ("is there a missing tooth clause for ortho", "up to two missing teeth"),
    ("how often are bitewings covered", "Bitewings are covered once per calendar year"),
    ("what age do sealants go to", "Sealants are covered up to age fourteen"),
    ("when was the last exam", "May 14, 2026"),
    ("is the deductible waived for preventive care", "waived for preventive"),
])
def test_a_question_in_the_callers_words_finds_its_entry(fixture, question, expected):
    assert expected in fixture.answer_questions(question)[0]["answer"]


def test_a_question_with_nothing_in_common_gets_nothing(fixture):
    assert fixture.answer_questions("what is the capital of France") == []
