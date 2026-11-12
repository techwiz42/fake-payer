"""The fixture file: valid, free of real data, covering the awkward cases, and extendable with no code change."""
from pathlib import Path

import pytest
import yaml

from fake_payer.coverage import FIXTURE_PATH, Fixture, FixtureError
from fake_payer.real_data_check import scan_text


def write(tmp_path, data):
    path = tmp_path / "fixture.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def raw():
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_the_shipped_fixture_loads_and_every_member_says_what_it_is_for():
    fixture = Fixture.load()
    assert len(fixture.members) >= 4
    assert all(member["note"].strip() for member in fixture.members.values())


def test_the_shipped_fixture_has_no_real_data():
    assert scan_text(FIXTURE_PATH.read_text(encoding="utf-8"), "fixture.yaml") == []


def test_the_awkward_cases_are_there_so_the_first_cut_is_not_only_the_happy_path():
    fixture = Fixture.load()
    assert any(m["status"] == "terminated" for m in fixture.members.values())
    assert any(any(count >= fixture.procedures[code]["frequency"]["times"] for code, count in (m.get("used") or {}).items() if fixture.procedures[code]["frequency"])
               for m in fixture.members.values())


def test_a_member_can_be_added_with_no_code_change(tmp_path):
    data = raw()
    data["members"]["M200001"] = {"first_name": "Added", "last_name": "Person", "date_of_birth": "1999-09-09", "plan": "SILVER", "group_number": "G20002",
                                  "status": "active", "effective_date": "2026-01-01", "note": "added by a test"}
    result = Fixture.load(write(tmp_path, data)).lookup(member_id="M200001", date_of_birth="1999-09-09", procedure_code="D2740")
    assert result["verified"] and result["procedure"]["coverage_percent"] == 40


@pytest.mark.parametrize("change,message", [
    (lambda d: d["members"]["M100001"].update(plan="PLATINUM"), "unknown plan"),
    (lambda d: d["members"]["M100001"].pop("note"), "missing note"),
    (lambda d: d["members"]["M100001"].update(date_of_birth="March 14"), "not a date"),
    (lambda d: d["members"]["M100003"].pop("termination_date"), "not a date"),
    (lambda d: d["members"]["M100001"].update(status="paused"), "active or terminated"),
    (lambda d: d["members"]["M100002"]["used"].update(D9999=1), "unknown code"),
    (lambda d: d["procedures"]["D0120"].update(category="cosmetic"), "category"),
    (lambda d: d["plans"]["GOLD"].pop("deductible"), "missing deductible"),
    (lambda d: d["members"]["M100001"].update(group_number="G99999"), "is not a group of plan"),
    (lambda d: d["members"]["M100001"].update(group_number="G20002"), "is not a group of plan"),
    (lambda d: d["groups"]["G10001"].update(plan="PLATINUM"), "unknown plan"),
    (lambda d: d["plans"]["GOLD"]["percent"].update(cosmetic=10), "not in the list of categories"),
])
def test_a_broken_fixture_is_refused_with_the_reason(tmp_path, change, message):
    data = raw()
    change(data)
    with pytest.raises(FixtureError) as error:
        Fixture.load(write(tmp_path, data))
    assert message in str(error.value)
