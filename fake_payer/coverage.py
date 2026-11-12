"""The fixture of fictional members, plans and coverage, and the one lookup the line answers from.

Every coverage fact the line speaks comes out of `Fixture.lookup`: the language model never supplies one (see persona.py). The same question about the same member
always returns the same answer.

The lookup works from whatever the caller has given. A member is identified by a member number or by name, together with a date of birth; the group number, the practice's
and provider's NPI and the last digits of the practice's tax ID are accepted and listed as still wanted when missing, but none of them is required. A valid group number on
its own is enough for the plan's benefits (without anything about a particular member). No procedure code is needed to ask what a plan pays.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .bridge import ToolFunction

FIXTURE_PATH = Path(__file__).with_name("fixture.yaml")

# What the line asks a caller for, in the order it is natural to ask. None of it is required.
ASKED_FOR = ("member_id", "date_of_birth", "patient_name", "group_number", "practice_npi", "provider_npi", "tin_last4")

UNVERIFIED_MESSAGE = "The member could not be verified."


class FixtureError(ValueError):
    """The fixture file is not usable."""


def _date(value: Any, where: str) -> str:
    try:
        return datetime.date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise FixtureError(f"{where}: not a date (YYYY-MM-DD)") from None


def _id(value: Any) -> str:
    """A member or group number as the payer compares it: no spaces, dashes or dots, upper case ("m-100 001" is "M100001")."""
    return re.sub(r"[\s\-.]", "", str(value or "")).upper()


def _name_tokens(value: Any) -> frozenset:
    return frozenset(re.findall(r"[a-z]+", str(value or "").lower()))


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


@dataclass(frozen=True)
class Fixture:
    plans: Dict[str, Dict[str, Any]]
    procedures: Dict[str, Dict[str, Any]]
    members: Dict[str, Dict[str, Any]]
    groups: Dict[str, Dict[str, Any]]
    categories: List[str]
    standing_answers: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = FIXTURE_PATH) -> "Fixture":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        plans, procedures, members = raw.get("plans") or {}, raw.get("procedures") or {}, raw.get("members") or {}
        groups, categories = raw.get("groups") or {}, list(raw.get("categories") or [])
        if not plans or not procedures or not members or not groups or not categories:
            raise FixtureError("the fixture needs categories, plans, groups, procedures and members")
        for plan_id, plan in plans.items():
            for key in ("name", "annual_maximum", "deductible", "percent"):
                if key not in plan:
                    raise FixtureError(f"plan {plan_id}: missing {key}")
            for category in plan["percent"]:
                if category not in categories:
                    raise FixtureError(f"plan {plan_id}: category {category!r} is not in the list of categories")
        for group_number, group in groups.items():
            if group.get("plan") not in plans:
                raise FixtureError(f"group {group_number}: unknown plan {group.get('plan')}")
        for code, procedure in procedures.items():
            if not str(code).startswith("D") or not procedure.get("description"):
                raise FixtureError(f"procedure {code}: needs a code like D0120 and a description")
            if procedure.get("category") not in categories:
                raise FixtureError(f"procedure {code}: category {procedure.get('category')!r} is not in the list of categories")
        for member_id, member in members.items():
            for key in ("first_name", "last_name", "date_of_birth", "plan", "group_number", "status", "effective_date", "note"):
                if key not in member:
                    raise FixtureError(f"member {member_id}: missing {key}")
            if member["plan"] not in plans:
                raise FixtureError(f"member {member_id}: unknown plan {member['plan']}")
            if member["group_number"] not in groups or groups[member["group_number"]]["plan"] != member["plan"]:
                raise FixtureError(f"member {member_id}: group {member['group_number']} is not a group of plan {member['plan']}")
            if member["status"] not in ("active", "terminated"):
                raise FixtureError(f"member {member_id}: status must be active or terminated")
            _date(member["date_of_birth"], f"member {member_id} date_of_birth")
            _date(member["effective_date"], f"member {member_id} effective_date")
            if member["status"] == "terminated":
                _date(member.get("termination_date"), f"member {member_id} termination_date")
            for code in (member.get("used") or {}):
                if code not in procedures:
                    raise FixtureError(f"member {member_id}: used lists unknown code {code}")
        return cls(plans=plans, procedures=procedures, members=members, groups=groups, categories=categories,
                   standing_answers={str(k): str(v) for k, v in (raw.get("standing_answers") or {}).items()})

    # ---- finding the member ---------------------------------------------------

    def _find_member(self, member_id: Any, date_of_birth: Any, patient_name: Any, group_number: Any) -> Optional[Dict[str, Any]]:
        """The member the details point to, or None. A member number or a name is needed, always with the date of birth; whatever else was given must agree."""
        dob = str(date_of_birth or "").strip()
        candidate = None
        if _id(member_id):
            candidate = next((m for key, m in self.members.items() if _id(key) == _id(member_id)), None)
        elif _name_tokens(patient_name) and dob:
            wanted = _name_tokens(patient_name)
            candidate = next((m for m in self.members.values() if {m["first_name"].lower(), m["last_name"].lower()} <= wanted and m["date_of_birth"] == dob), None)
        if candidate is None or dob != _date(candidate["date_of_birth"], "date_of_birth"):
            return None
        if _name_tokens(patient_name) and not ({candidate["first_name"].lower(), candidate["last_name"].lower()} <= _name_tokens(patient_name)):
            return None
        if _id(group_number) and _id(group_number) != _id(candidate["group_number"]):
            return None
        return candidate

    def _accept_unknown(self, member_id: Any, date_of_birth: Any, patient_name: Any, group_number: Any) -> Optional[Dict[str, Any]]:
        """Any patient the caller names is a member: an ordinary active one on the group's plan (the first plan when the group is not one of ours), nothing used yet. Only a
        member number that belongs to a fixture member, given with details that do not agree, is still refused; so is a caller who has not said who the patient is."""
        if _id(member_id) and any(_id(key) == _id(member_id) for key in self.members):
            return None
        dob = str(date_of_birth or "").strip()
        tokens = re.findall(r"[a-z]+", str(patient_name or "").lower())
        if not dob or not (_id(member_id) or tokens):
            return None
        try:
            _date(dob, "date_of_birth")
        except FixtureError:
            return None
        group = next((g for key, g in self.groups.items() if _id(key) == _id(group_number)), None) if _id(group_number) else None
        plan_key = group["plan"] if group is not None else next(iter(self.plans))
        names = str(patient_name or "").split()
        return {"first_name": names[0] if names else "", "last_name": names[-1] if len(names) > 1 else "", "date_of_birth": dob, "plan": plan_key,
                "group_number": group_number or "", "status": "active", "effective_date": "2025-01-01", "annual_maximum_used": 0, "deductible_met": 0, "used": {}}

    # ---- the lookup -----------------------------------------------------------

    def lookup(self, member_id: Optional[str] = None, date_of_birth: Optional[str] = None, patient_name: Optional[str] = None, group_number: Optional[str] = None,
               practice_npi: Optional[str] = None, provider_npi: Optional[str] = None, tin_last4: Optional[str] = None, procedure_code: Optional[str] = None) -> Dict[str, Any]:
        """What the payer would say. Everything is optional. A member who cannot be found and one whose details do not agree get the same answer, so the line never says which
        detail was wrong; the answer depends only on what was given."""
        given = {"member_id": member_id, "date_of_birth": date_of_birth, "patient_name": patient_name, "group_number": group_number,
                 "practice_npi": practice_npi, "provider_npi": provider_npi, "tin_last4": tin_last4}
        out: Dict[str, Any] = {"still_needed": [name for name in ASKED_FOR if not str(given[name] or "").strip()], "required_to_continue": False}
        if self.standing_answers:
            out["standing_answers"] = dict(self.standing_answers)
        notes = []
        for name, digits in (("practice_npi", 10), ("provider_npi", 10), ("tin_last4", 4)):
            if str(given[name] or "").strip() and len(_digits(given[name])) != digits:
                notes.append(f"{name} should be {digits} digits")
        if notes:
            out["notes"] = notes

        member = self._find_member(member_id, date_of_birth, patient_name, group_number)
        if member is None:
            member = self._accept_unknown(member_id, date_of_birth, patient_name, group_number)
        if member is None:
            out.update(verified=False, message=UNVERIFIED_MESSAGE)
            group = next((g for key, g in self.groups.items() if _id(key) == _id(group_number)), None) if _id(group_number) else None
            if group is not None:                                  # a valid group number alone is enough for what the plan pays
                plan = self.plans[group["plan"]]
                out.update(plan_level_only=True, plan=self._plan_block(plan, None), benefits=self._benefits(plan, None))
            return out

        eligibility: Dict[str, Any] = {"status": member["status"], "effective_date": _date(member["effective_date"], "effective_date")}
        out.update(verified=True, member={"first_name": member["first_name"], "last_name": member["last_name"]}, eligibility=eligibility)
        if member["status"] == "terminated":
            eligibility["termination_date"] = _date(member["termination_date"], "termination_date")
            out["coverage_available"] = False
            return out
        plan = self.plans[member["plan"]]
        out["plan"] = self._plan_block(plan, member)
        out["benefits"] = self._benefits(plan, member)
        if procedure_code:
            out["procedure"] = self._procedure(member, plan, str(procedure_code).strip().upper(), out["plan"]["annual_maximum_remaining"])
        return out

    # ---- pieces of the answer -------------------------------------------------

    @staticmethod
    def _plan_block(plan: Dict[str, Any], member: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        block: Dict[str, Any] = {"name": plan["name"], "annual_maximum": plan["annual_maximum"], "deductible": plan["deductible"]}
        if "family_deductible" in plan:
            block["family_deductible"] = plan["family_deductible"]
        if "orthodontic_lifetime_maximum" in plan:
            block["orthodontic_lifetime_maximum"] = plan["orthodontic_lifetime_maximum"]
        if member is not None:
            block["annual_maximum_remaining"] = max(0, plan["annual_maximum"] - member.get("annual_maximum_used", 0))
            block["deductible_remaining"] = max(0, plan["deductible"] - member.get("deductible_met", 0))
        return block

    def _benefits(self, plan: Dict[str, Any], member: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """What the plan pays by category, with the services in each and their frequency limits: enough to answer "what is covered" with no procedure code."""
        out: Dict[str, Any] = {}
        for category in self.categories:
            if category not in plan["percent"]:
                out[category] = {"covered": False}
                continue
            services = []
            for code, procedure in self.procedures.items():
                if procedure["category"] != category:
                    continue
                entry: Dict[str, Any] = {"code": code, "description": procedure["description"]}
                frequency = procedure.get("frequency")
                if frequency:
                    entry["frequency_limit"] = {"times": frequency["times"], "period": frequency["period"]}
                    if member is not None:
                        used = (member.get("used") or {}).get(code, 0)
                        entry["times_used"], entry["times_remaining"] = used, max(0, frequency["times"] - used)
                services.append(entry)
            out[category] = {
                "covered": True,
                "coverage_percent": plan["percent"][category],
                "deductible_applies": category not in (plan.get("deductible_waived_for") or []),
                "waiting_period_months": (plan.get("waiting_period_months") or {}).get(category),
                "services": services,
            }
        return out

    def _procedure(self, member: Dict[str, Any], plan: Dict[str, Any], code: str, maximum_remaining: int) -> Dict[str, Any]:
        procedure = self.procedures.get(code)
        if procedure is None:
            return {"code": code, "found": False}
        if procedure["category"] not in plan["percent"]:
            return {"code": code, "found": True, "description": procedure["description"], "category": procedure["category"], "covered_now": False,
                    "reason": "the plan has no benefit for this category"}
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
        "Look up what a dental plan pays. Pass whatever the caller has given so far; every field is optional, and you can call this again as you learn more. A member is found by "
        "member number or by name, together with the date of birth (YYYY-MM-DD); a group number on its own is enough for the plan's benefits. The result says whether the member "
        "was verified, lists what has still not been given (`still_needed`), and, for a verified member, gives eligibility, the annual maximum and deductible with what remains, "
        "and the benefits by category (preventive, basic, major, orthodontics) with the services in each and their frequency limits. A procedure code is NOT needed to ask what "
        "is covered; give one only to check a specific procedure. Every coverage fact you state must come from this tool."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "member_id": {"type": "string", "description": "The member number the caller gave, for example M100001."},
            "date_of_birth": {"type": "string", "description": "The patient's date of birth as YYYY-MM-DD."},
            "patient_name": {"type": "string", "description": "The patient's full name as the caller said it."},
            "group_number": {"type": "string", "description": "The group number, for example G10001."},
            "practice_npi": {"type": "string", "description": "The practice's ten-digit NPI."},
            "provider_npi": {"type": "string", "description": "The treating provider's ten-digit NPI."},
            "tin_last4": {"type": "string", "description": "The last four digits of the practice's tax ID."},
            "procedure_code": {"type": "string", "description": "Optional. A procedure code such as D0120, to check one specific procedure."},
        },
        "required": [],
    },
}


def make_tools(fixture: Fixture) -> Dict[str, Any]:
    """The tool mapping the bridge runs: name -> (definition, async function)."""

    async def lookup_coverage(arguments: Dict[str, Any]) -> Dict[str, Any]:
        wanted = {name: arguments.get(name) for name in (*ASKED_FOR, "procedure_code")}
        return fixture.lookup(**wanted)

    return {TOOL_DEFINITION["name"]: (TOOL_DEFINITION, lookup_coverage)}
