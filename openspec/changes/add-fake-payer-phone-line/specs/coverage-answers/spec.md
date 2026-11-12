# Spec Delta

## Purpose
How the fake insurer answers coverage questions: from a fixed set of fictional members and plans, so the same question always gets the same answer.

## ADDED Requirements

### Requirement: Answers come from the fixture
Every statement the line makes about a member's eligibility, plan, deductible, annual maximum, coverage percentage or frequency limit SHALL come from the fixture through a lookup. The language model SHALL NOT supply or alter such a value.

#### Scenario: Same question twice
- **WHEN** two calls ask the coverage percentage for the same member and procedure code
- **THEN** both are given the same value

#### Scenario: Something the fixture does not hold
- **WHEN** the caller asks about a member or procedure code the fixture does not contain
- **THEN** the line says it cannot find it, and does not guess

### Requirement: Identity check
Before giving member-specific answers the line SHALL ask for the member identifier and the member's date of birth, and SHALL answer only when both match a fixture member.

#### Scenario: The details match
- **WHEN** the caller gives a member identifier and date of birth that match a fixture member
- **THEN** the line proceeds to answer that member's coverage questions

#### Scenario: The details do not match
- **WHEN** the date of birth does not match the member identifier
- **THEN** the line says it cannot verify the member, and gives no coverage values

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
