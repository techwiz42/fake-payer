# Design

## Context

See proposal.md for why. The caller this is built to rehearse is an automated assistant that places a call to an insurer's provider line, identifies a patient, asks for coverage values and writes down what it is told. That caller already exists elsewhere; this program only has to be a believable, repeatable thing on the other end of a phone number. It reaches it by dialing, so there is no shared code, database or network path between them.

agent_framework already carries a working inbound phone call stack: Vonage delivers the call's audio as 16-bit PCM at 8 kHz over a websocket; Soniox's real-time speech-to-text and text-to-speech work in the same format, so nothing is transcoded; a streaming Claude client runs the turn; a bridge ties them together with turn taking, barge-in and a watchdog. This program is that stack with the platform taken out.

Constraints: the line is public, so cost and abuse are real; answers must be exact enough to assert on; the repository is public, so nothing real may be in it.

## Goals / Non-Goals

**Goals:**
- A number that answers, talks naturally enough that an automated caller's turn taking is really exercised, and gives repeatable answers.
- As little new code as possible: copy what works, delete what is platform.
- Small and cheap enough for one person to run.

**Non-Goals:**
- Any user interface or web page. (Vonage needs two network endpoints to deliver a call, see decision 2; that is plumbing, not a web application.)
- Simulating any real insurer's plan terms, menu wording or voice.
- Anything but voice: no text messages, fax or portal.
- Being a product or being composed into customer applications.

## Decisions

**1. Borrow, by copying, with the source noted.** Files are copied into this repository (not imported, since the source is private and this one is public), each with a header naming the file and agent_framework commit it came from. What is copied:

| From agent_framework | Used for | Change |
|---|---|---|
| `core/soniox_streaming_client.py` (+ its tests) | Real-time speech-to-text and text-to-speech over websockets | none; depends only on `websockets` |
| `core/anthropic_streaming_client.py` (+ its tests) | Streaming model turns with tool calls | replace the three constants it takes from the platform's client module (key, default model, URL) with local configuration |
| `modules/communication/backend/soniox_ncco.py` | The answer response telling Vonage to stream audio as PCM 8 kHz | none expected |
| `modules/communication/backend/soniox_stitched_bridge.py` | The turn state machine, barge-in, streaming reply to speech, call watchdog | strip the privacy-redaction step, tenant configuration, call persistence, payer-call control and placeholder restoring; replace the tool dispatcher with the single coverage lookup |
| `modules/communication/backend/greetings.py` | Opening line | replace with the fictional provider's disclosure |

Not copied: anything about tenants, patients, redaction, scheduling or the platform's database. Each copied file is read in full before it goes in, so nothing internal comes along in a comment or default.

**2. Telephony: one number at a hosted voice API, delivered by two endpoints.** An inbound call makes the provider fetch an answer response from us (an HTTP request) and then open an audio websocket to us. Both are served by one small asyncio server with no pages and no other routes. Alternative considered: running our own SIP server, which is far more to run and secure for no gain at this volume.

**3. Speech and model.** Soniox real-time speech-to-text and text-to-speech (as decided by the owner); the language model is Claude through the copied streaming client. The voice is Soniox's "Mark", set in configuration and used for every utterance, including the disclosure and spoken refusals. The reply is streamed into text-to-speech as it is produced, which is what makes the line feel live.

**4. Facts come from a lookup, not from the model.** The model is told the persona and given one tool, `lookup_coverage(member_id, date_of_birth, procedure_code)`, that reads the fixture and returns a record or "not found". It is told never to state a coverage value that did not come from a tool result. A test replays transcripts and fails if a number in the representative's speech is not in the tool results.

**5. The fixture is a plain data file** (YAML or JSON) of members, plans and coverage by procedure code (percentage, deductible, annual maximum, frequency limit), with a note on each member. A few members are deliberately awkward (terminated coverage, a frequency limit already used) so the first cut is not only the happy path.

**6. Limits are enforced by the program, not trusted to the model.** The simultaneous-call count, per-number hourly count, call length and daily spend estimate are kept by the program. Refusals happen before any speech or model service is touched. The copied watchdog already enforces the length limit.

**7. Call log: one JSON file per call in a local directory,** keyed by the reference number, plus a small command to list and print them and to remove old ones. No database, no web page.

**8. Secrets and numbers live outside the repository.** Provider, Soniox and model credentials, and the number itself, come from the environment. The repository ships an example configuration with obviously fictional values. Fixture telephone numbers use the reserved fictional range.

## Risks / Trade-offs

- **Open to everyone.** Anyone who finds the number can run up cost. Mitigation: simultaneous-call cap, per-number rate limit, length limit and a daily ceiling, with a spoken refusal that costs nothing.
- **Recording strangers.** Anyone may call, so the line records people who are not us. Mitigation: the disclosure on every call, a short retention period, and nothing in the log beyond the masked number, transcript and stated values.
- **Mistaken for a real insurer.** Mitigation: a fictional name and the disclosure.
- **Copied code drifts from its source.** The source keeps changing. Mitigation: the header names the commit; we do not track upstream, and a fix found here is carried back by hand if it matters.
- **Slimming the bridge.** The copied bridge is 961 lines, much of it platform. Cutting it wrongly could break barge-in or the watchdog. Mitigation: keep its tests where they apply, and a recorded test call after the cut (task 2.4).
- **Realism.** One cooperative representative teaches the caller little about real payers. That is the accepted cost of a minimal first cut; see Later.

## Later (not in this change)

A second change adds the harder behaviors once the first cut is working: a spoken phone menu with keypad tones and a dead end, hold time, identity questions that need more than a member number, a representative who refuses or demands a human, a transfer, a hang-up, and a conflicting value. A scenario would be chosen by an extension or a member number, and each would be a short script of steps.

## Open Questions

1. **Where it runs.** A container on an existing virtual machine, or a small managed container service. The second keeps it off machines that run other things. Task 6.1 settles this.
2. **Which model.** The default Claude model in the copied client, or a smaller, faster one. A short model is enough for a lookup-and-answer persona; to be settled by the first recorded call's latency.
