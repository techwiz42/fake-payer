"""The fixture of fictional members, plans and coverage, and the one lookup the line answers from.

Every coverage fact the line speaks comes out of `Fixture.lookup`: the language model never supplies one (see persona.py). The same question about the same member
always returns the same answer.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .bridge import ToolFunction

FIXTURE_PATH = Path(__file__).with_name("fixture.yaml")

UNVERIFIED = {"verified": False, "message": "The member could not be verified."}


class FixtureError(ValueError):
    """The fixture file is not usable."""


def _date(value: Any, where: str) -> str:
    try:
        return datetime.date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise FixtureError(f"{where}: not a date (YYYY-MM-DD)") from None


@dataclass(frozen=True)
class Fixture:
    plans: Dict[str, Dict[str, Any]]
    procedures: Dict[str, Dict[str, Any]]
    members: Dict[str, Dict[str, Any]]

    @classmethod
    def load(cls, path: Path = FIXTURE_PATH) -> "Fixture":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        plans, procedures, members = raw.get("plans") or {}, raw.get("procedures") or {}, raw.get("members") or {}
        if not plans or not procedures or not members:
            raise FixtureError("the fixture needs plans, procedures and members")
        for plan_id, plan in plans.items():
            for key in ("name", "annual_maximum", "deductible", "percent"):
                if key not in plan:
                    raise FixtureError(f"plan {plan_id}: missing {key}")
        for code, procedure in procedures.items():
            if not str(code).startswith("D") or not procedure.get("description"):
                raise FixtureError(f"procedure {code}: needs a code like D0120 and a description")
            for plan_id, plan in plans.items():
                if procedure.get("category") not in plan["percent"]:
                    raise FixtureError(f"procedure {code}: category {procedure.get('category')!r} is not in plan {plan_id}")
        for member_id, member in members.items():
            for key in ("first_name", "last_name", "date_of_birth", "plan", "status", "effective_date", "note"):
                if key not in member:
                    raise FixtureError(f"member {member_id}: missing {key}")
            if member["plan"] not in plans:
                raise FixtureError(f"member {member_id}: unknown plan {member['plan']}")
            if member["status"] not in ("active", "terminated"):
                raise FixtureError(f"member {member_id}: status must be active or terminated")
            _date(member["date_of_birth"], f"member {member_id} date_of_birth")
            _date(member["effective_date"], f"member {member_id} effective_date")
            if member["status"] == "terminated":
                _date(member.get("termination_date"), f"member {member_id} termination_date")
            for code in (member.get("used") or {}):
                if code not in procedures:
                    raise FixtureError(f"member {member_id}: used lists unknown code {code}")
        return cls(plans=plans, procedures=procedures, members=members)

    def lookup(self, member_id: str, date_of_birth: str, procedure_code: Optional[str] = None) -> Dict[str, Any]:
        """What the payer would say. A member who cannot be found and one whose date of birth does not match get the same answer, so the line never says which."""
        member = self.members.get(str(member_id or "").strip().upper())
        if member is None or str(date_of_birth or "").strip() != _date(member["date_of_birth"], "date_of_birth"):
            return dict(UNVERIFIED)
        eligibility: Dict[str, Any] = {"status": member["status"], "effective_date": _date(member["effective_date"], "effective_date")}
        out: Dict[str, Any] = {"verified": True, "member": {"first_name": member["first_name"], "last_name": member["last_name"]}, "eligibility": eligibility}
        if member["status"] == "terminated":
            eligibility["termination_date"] = _date(member["termination_date"], "termination_date")
            out["coverage_available"] = False
            return out
        plan = self.plans[member["plan"]]
        maximum_remaining = max(0, plan["annual_maximum"] - member.get("annual_maximum_used", 0))
        out["plan"] = {
            "name": plan["name"],
            "annual_maximum": plan["annual_maximum"],
            "annual_maximum_remaining": maximum_remaining,
            "deductible": plan["deductible"],
            "deductible_remaining": max(0, plan["deductible"] - member.get("deductible_met", 0)),
        }
        if procedure_code:
            out["procedure"] = self._procedure(member, plan, str(procedure_code).strip().upper(), maximum_remaining)
        return out

    def _procedure(self, member: Dict[str, Any], plan: Dict[str, Any], code: str, maximum_remaining: int) -> Dict[str, Any]:
        procedure = self.procedures.get(code)
        if procedure is None:
            return {"code": code, "found": False}
        frequency = procedure.get("frequency")
        used = (member.get("used") or {}).get(code, 0)
        result: Dict[str, Any] = {
            "code": code,
            "found": True,
            "description": procedure["description"],
            "category": procedure["category"],
            "coverage_percent": plan["percent"][procedure["category"]],
            "deductible_applies": procedure["category"] not in (plan.get("deductible_waived_for") or []),
            "frequency_limit": None,
            "covered_now": True,
        }
        if frequency:
            remaining = max(0, frequency["times"] - used)
            result["frequency_limit"] = {"times": frequency["times"], "period": frequency["period"], "times_used": used, "times_remaining": remaining}
            if remaining == 0:
                result["covered_now"], result["reason"] = False, "the frequency limit has been reached"
        if result["covered_now"] and maximum_remaining == 0:
            result["covered_now"], result["reason"] = False, "the annual maximum has been reached"
        return result


TOOL_DEFINITION: Dict[str, Any] = {
    "name": "lookup_coverage",
    "description": (
        "Look up a member's coverage. Needs the member's identifier and date of birth (YYYY-MM-DD); returns whether the member could be verified and, when verified, "
        "eligibility, the plan's annual maximum and deductible with what remains, and, if a procedure code is given, the coverage percentage, frequency limit and "
        "whether it is covered now. Every coverage fact you state must come from this tool."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "member_id": {"type": "string", "description": "The member identifier the caller gave, for example M100001."},
            "date_of_birth": {"type": "string", "description": "The member's date of birth as YYYY-MM-DD."},
            "procedure_code": {"type": "string", "description": "Optional. A procedure code such as D0120."},
        },
        "required": ["member_id", "date_of_birth"],
    },
}


def make_tools(fixture: Fixture) -> Dict[str, Any]:
    """The tool mapping the bridge runs: name -> (definition, async function)."""

    async def lookup_coverage(arguments: Dict[str, Any]) -> Dict[str, Any]:
        return fixture.lookup(arguments.get("member_id", ""), arguments.get("date_of_birth", ""), arguments.get("procedure_code"))

    return {TOOL_DEFINITION["name"]: (TOOL_DEFINITION, lookup_coverage)}
