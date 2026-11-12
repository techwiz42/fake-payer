# Spec Delta

## Purpose
How the fake insurer answers coverage questions: from a fixed set of fictional members and plans, so the same question always gets the same answer.

## ADDED Requirements

### Requirement: Answers come from the fixture
Every statement the line makes about a member's eligibility, plan, deductible, annual maximum, coverage percentage, waiting period or frequency limit SHALL come from the fixture through a lookup. The language model SHALL NOT supply or alter such a value.

#### Scenario: Same question twice
- **WHEN** two calls ask the coverage percentage for the same member and procedure code
- **THEN** both are given the same value

#### Scenario: Something the fixture does not hold
- **WHEN** the caller asks about a member or procedure code the fixture does not contain
- **THEN** the line says it cannot find it, and does not guess

### Requirement: Any named patient is a member
Because the line stands in for whichever insurer a platform under test calls, it SHALL accept any patient the caller names (a member number or a name, with a date of birth) as an ordinary active member of the group's plan (the first plan when the group is not one of the fixture's), with nothing used yet. Only a member number that belongs to a fixture member, given with details that disagree, SHALL be refused, so the fixture's scenario members stay testable. A caller who has not said who the patient is SHALL NOT be verified.

### Requirement: Identifying the member
Before giving member-specific answers the line SHALL identify the member from a member number or the patient's name, together with the patient's date of birth. Whatever else the caller gives (the name with a member number, the group number) SHALL agree with the member, or the member is not verified. The practice's and the provider's NPI and the last digits of the practice's tax ID SHALL be accepted but SHALL NOT affect whether the member is verified.

#### Scenario: A member number and date of birth
- **WHEN** the caller gives a member number and date of birth that match a fixture member
- **THEN** the line proceeds to answer that member's questions

#### Scenario: A name and date of birth, without a member number
- **WHEN** the caller gives a patient's name and date of birth that match a fixture member
- **THEN** the member is verified

#### Scenario: A detail that does not agree
- **WHEN** the date of birth is missing or wrong, or the name or group number is another member's
- **THEN** the line says it cannot verify the member and gives no member-specific figures, and it does not say which detail was wrong

### Requirement: Asking for what is missing, never requiring it
The line SHALL ask the caller for the member number, the patient's date of birth and name, the group number, the practice's NPI, the provider's NPI and the last four digits of the practice's tax ID when they have not been given, a few at a time, starting with what identifies the patient. It SHALL NOT refuse to continue because one is missing: it answers what it can from what it has. Every lookup SHALL list what has still not been given.

#### Scenario: Details are missing
- **WHEN** the caller has given a member number and date of birth but not the NPIs or tax ID
- **THEN** the member is verified, the answer lists the missing details, and the line asks for them without holding back its answer

#### Scenario: The caller will not give something
- **WHEN** the caller says it cannot give a detail
- **THEN** the line says it can go on with what it has

### Requirement: Benefits need no procedure code
The line SHALL answer what a plan covers by category (preventive, basic, major, orthodontics) and by service, with the percentages, deductible, annual maximum, waiting periods and frequency limits, without a procedure code. A procedure code SHALL be used only when the caller gives one, to check a specific procedure.

#### Scenario: A question about a kind of care
- **WHEN** a verified caller asks what the plan pays for fillings
- **THEN** the answer comes from the plan's basic-care benefit and no code is asked for

#### Scenario: A plan without a benefit
- **WHEN** the plan has no benefit for a category, such as orthodontics
- **THEN** the line says the plan has no benefit for it

### Requirement: A group number gives the plan's benefits
A valid group number given without a verified member SHALL be enough for the plan's general benefits, which the line MAY share, saying they are the plan's and not the patient's own. It SHALL give nothing about eligibility, what a member has used or what remains.

#### Scenario: Only a group number
- **WHEN** the caller gives a valid group number and no member details
- **THEN** the line gives the plan's benefits and says it cannot give anything specific to the patient

### Requirement: Representative and reference
The line SHALL give a fictional representative name when the caller reaches the representative, and a call reference number, and SHALL repeat either on request. The reference number SHALL be the same each time it is given within one call.

#### Scenario: The caller asks for a reference
- **WHEN** the caller asks for a reference number twice in one call
- **THEN** the same number is given both times

### Requirement: Fixture is data, not code
The fixture SHALL be a human-readable data file listing members, plans and per-procedure-code coverage, each member with a note on what it is for. Adding a member SHALL need no code change.

#### Scenario: A member is added
- **WHEN** a member is added to the fixture file
- **THEN** the next call can ask about that member with no other change
