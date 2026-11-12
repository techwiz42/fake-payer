# Tasks

## 1. Repository and configuration

- [x] 1.1 Project skeleton: package, tests, example configuration with fictional values, a README saying this is a test fixture and not a product. Verify: tests run from a clean checkout.
- [x] 1.2 Configuration read from the environment (provider, Soniox and model credentials, the number, limits). Verify: a test shows the program refuses to start with a missing credential and never logs one.
- [x] 1.3 A check that fails the build if the repository contains a real insurer's name (from a list kept in the check), a telephone number outside the fictional ranges, an internal host name, or anything shaped like a credential. Verify: a seeded violation of each kind fails it.

## 2. Bring in the voice code

- [x] 2.1 Copy `soniox_streaming_client.py` and its tests, with the source header. Verify: its tests pass here.
- [x] 2.2 Copy `anthropic_streaming_client.py` and its tests, replacing the platform constants with local configuration. Verify: its tests pass here.
- [x] 2.3 Copy `soniox_ncco.py`. Verify: it produces the answer response with the PCM 8 kHz content type, checked by a test.
- [x] 2.4 Copy `soniox_stitched_bridge.py` and cut it down: remove redaction, tenant configuration, persistence, payer-call control and placeholder restoring; keep turn taking, barge-in, streaming reply and the watchdog. Verify: a scripted-audio test covers a turn and a barge-in; the file has no import from the platform.
- [x] 2.5 Text-to-speech voice set to "Adrian" from configuration, for every utterance. Verify: a test shows each text-to-speech request, including the disclosure and a refusal, names that voice.
- [x] 2.6 Opening line replaced by the fictional provider's disclosure. Verify: a test transcript begins with it.

## 3. Answering a call

- [x] 3.1 One small server with the answer request and the audio websocket, and nothing else. Verify: a local simulated Vonage connection gets an answer response and an open audio stream; any other path is refused.
- [x] 3.2 Run a call through the bridge end to end with the lookup tool in place of the platform's dispatcher. Verify: a simulated caller asks a coverage question and is answered by speech.

- [x] 3.3 Only a call that came through the answer request can open the audio websocket: the answer request needs a secret key in its URL and signs a short-lived token into the websocket address; the websocket refuses anything without a valid one. Caller numbers are kept only as a keyed hash and the last four digits. Verify: tests for a missing key, a wrong key, no token, a token for another call, a forged and an expired token; no speech service is touched by a refused connection.

## 4. Coverage answers

- [x] 4.1 Fixture file format and a first set of members, plans and procedure codes, each member with a note, including a terminated member and a used-up frequency limit. Verify: a schema check and the "no real data" check pass.
- [x] 4.2 `lookup_coverage` and the identity check (member identifier, date of birth). Verify: match, mismatch and not-found tests.
- [x] 4.3 Persona prompt that forbids stating values not returned by the tool, plus the representative name and a stable call reference. Verify: a replay test fails when a number in the speech is not in the tool results; the same reference is repeated on request.

- [x] 4.4 The lookup works from whatever is given: a member is identified by member number or by name, with the date of birth; group number, practice and provider NPI and the last digits of the tax ID are accepted, listed as still wanted when missing, and never required; a valid group number alone gives the plan's benefits. Verify: tests for each combination, for the wrong-detail answer leaking nothing, and for NPIs and tax ID not changing the outcome.
- [x] 4.5 Benefits by category with the services and limits in each, no procedure code needed; the fixture has groups, family deductibles, waiting periods and an orthodontic benefit with a lifetime maximum, shaped like commonly described dental plans. Verify: tests that a benefits question is answered with no code, and that a plan without orthodontics says so.
- [x] 4.6 The persona asks for the missing details, a few at a time, and does not insist; it does not ask for a procedure code. Verify: prompt tests.

## 5. Limits and log

- [x] 5.1 Simultaneous-call cap, per-number hourly count (calls with no number share one), call length limit, daily spend ceiling, each with a spoken refusal that uses no speech or model service. Verify: tests with a fake clock for each limit.
- [x] 5.2 Call log: one file per call with the contents the spec lists. Verify: a test call produces one complete entry with the number masked.
- [x] 5.3 Command to list and print calls by reference, and to remove old ones (retention). Verify: a known reference prints its call; an old entry is removed and a recent one kept.

## 6. Hosting and number

- [x] 6.1 Decide where it runs (open question 1), write a container build and a deploy script. Verify: a deploy from a clean checkout answers the provider's request over HTTPS. (Its own e2-micro VM. Checked from outside: valid certificate, the answer request returns the connect response, and a real connection played the disclosure, first audio in 0.77 s.)
- [ ] 6.2 Obtain the number, set the limits, point the provider at the program. Verify: a real call from a phone is answered with the disclosure.
- [ ] 6.3 Settle the model (open question 2) from the latency of a recorded call. Verify: the measured time from the caller finishing to the first spoken word is written in the repository.

## 7. Use

- [ ] 7.1 A short written guide: the number, the fixture members and what each is for, how to read a call. Verify: someone who has not seen the code can place a call and read it back.
- [ ] 7.2 One run from the calling system against the cooperative case, recorded in the repository. Verify: the log shows what the caller was told, and the caller's own records match it.
