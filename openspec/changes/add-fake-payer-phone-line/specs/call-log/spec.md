# Spec Delta

## Purpose
A record of each call so a test can assert what was said.

## ADDED Requirements

### Requirement: What is logged
For every call the line SHALL record the time, the caller number masked except its last four digits, the full transcript with speaker turns, the call reference number, the values the representative stated, and how the call ended (caller hung up, time limit, refused for a limit).

#### Scenario: A call completes
- **WHEN** a call ends for any reason
- **THEN** one log entry exists with all of the above

### Requirement: Reading it back
A command SHALL print a call by its reference number as data, and list recent calls.

#### Scenario: A test fetches the call
- **WHEN** the command is given a reference number
- **THEN** it prints that call's transcript and stated values

### Requirement: Retention
Log entries SHALL be removed after a configured number of days, because the line records the voices and words of anyone who calls.

#### Scenario: An old entry
- **WHEN** an entry is older than the retention period
- **THEN** it is removed on the next cleanup
