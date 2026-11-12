# Spec Delta

## Purpose
How the fake insurer's telephone line answers a call from anyone, and the limits that keep a publicly reachable test line from becoming a cost or abuse problem.

## ADDED Requirements

### Requirement: Answering and disclosure
The line SHALL answer an inbound call, speak a short disclosure naming the provider as fictional and the call as recorded and transcribed, and then take the caller's questions. The provider's name SHALL be fictional and SHALL NOT be, or resemble, a real insurer's name.

#### Scenario: A call comes in
- **WHEN** anyone dials the number
- **THEN** the line answers, speaks the disclosure, and is ready for the caller to speak

#### Scenario: The caller asks who this is
- **WHEN** the caller asks which company this is
- **THEN** the answer is the fictional provider's name and nothing that identifies a real insurer

### Requirement: Only real calls
Only a call that came through the line's answer request SHALL be able to open the call's audio stream. The answer request SHALL be answered only when it carries the configured secret key, and SHALL sign a short-lived token into the audio stream's address; the audio stream SHALL be refused without a valid, unexpired token for that call. A refused connection SHALL use no speech or model service. Caller numbers SHALL be kept only as a keyed hash (to count calls per number) and their last four digits (for the log).

#### Scenario: Someone connects to the audio stream directly
- **WHEN** a connection to the audio stream carries no token, a token for another call, or a forged or expired one
- **THEN** it is refused before anything is spoken, and no speech or model service is used

#### Scenario: An answer request without the key
- **WHEN** the answer request does not carry the secret key
- **THEN** it is refused and no token is issued

### Requirement: Voice
The line SHALL speak every utterance, including the disclosure and the spoken refusals, in the Soniox voice named "Adrian".

#### Scenario: Any utterance
- **WHEN** the line speaks, for any reason
- **THEN** it is the voice "Adrian"

### Requirement: Talking over the line
The caller SHALL be able to speak while the line is speaking. The line SHALL stop speaking, drop what it had not yet said, and listen.

#### Scenario: Barge-in
- **WHEN** the caller starts speaking while the line is mid-sentence
- **THEN** the line stops at once and answers what the caller said

### Requirement: Limits on volume and spend
The line SHALL enforce a maximum number of simultaneous calls, a maximum number of calls per caller number per hour (calls with no number sharing one count), a maximum call length, and a daily spend ceiling covering telephony, speech and model usage. When a limit stops a call the line SHALL say so in a short spoken message first. A call refused for volume or spend SHALL use no speech or model service.

#### Scenario: Too many calls at once
- **WHEN** a call arrives while the maximum number of calls is in progress
- **THEN** it is answered with a short spoken "busy, please try again later" and ended

#### Scenario: One number calls repeatedly
- **WHEN** a caller number has made the maximum calls in the past hour and calls again
- **THEN** the call is ended after a short spoken refusal

#### Scenario: A call runs past the length limit
- **WHEN** a call reaches the maximum length
- **THEN** the line says the time is up and ends the call

#### Scenario: The daily ceiling is reached
- **WHEN** estimated spend for the day reaches the ceiling
- **THEN** further calls are refused with a spoken message until the next day, and each refusal is logged

### Requirement: No real data
The line SHALL carry only fictional data. The repository SHALL contain no real payer's name, telephone number, plan terms, member data or credentials.

#### Scenario: Fixtures are checked
- **WHEN** the test suite runs
- **THEN** a check fails if a fixture contains a telephone number outside the reserved fictional ranges, or a name on a configured list of real insurers
