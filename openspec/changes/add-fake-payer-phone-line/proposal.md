# Proposal

## Why

Automated calls to dental insurers (checking coverage, eligibility and benefits by phone) have only been rehearsed against a person playing the representative. A fictional insurer on a real phone number lets the same call be made again and again, with answers a test can check, and without taking up a person's time.

## What Changes

- A new standalone program (no user interface, no web pages) that owns a telephone number. When called it answers as a **fictional** insurance provider, listens, and answers coverage questions.
- **First cut is deliberately minimal:** one cooperative representative. No phone menu, no hold, no refusals or transfers. Those come later (see design.md, "Later").
- Answers come from a **fixture** of fictional members, plans and coverage by procedure code, through a lookup, never invented by the language model. The same question about the same member gets the same answer every time, so a test can say what a caller should have recorded.
- **Built from the voice code already in agent_framework**, copied in: the real-time Soniox speech-to-text and text-to-speech clients, the streaming language-model client, the Vonage answer response, and a slimmed copy of the call bridge (turn taking, barge-in, call watchdog). Soniox does both speech directions.
- **Anyone may call the number.** Because of that, the line limits cost and abuse itself: a spoken disclosure, a cap on simultaneous calls, a per-caller rate limit, a maximum call length and a daily spend ceiling.
- A simple call log on disk (transcript, the values stated, how the call ended), readable by command.
- **Not** part of any product. It is a test fixture. It uses synthetic data only and never carries a real payer's name, number or plan terms.

## Capabilities

### New Capabilities

- `phone-line`: answering an inbound call from anyone, the spoken disclosure, and limits on volume and spend.
- `coverage-answers`: the fixture of fictional members, plans and coverage, the identity check, consistent answers, and the representative's name and call reference.
- `call-log`: what is recorded for each call and how to read it back.

### Modified Capabilities

None. This repository starts empty.

## Impact

- New repository, new telephone number, and the two endpoints Vonage requires (an answer request and an audio websocket) on one small server. No pages, no UI.
- Credentials for the telephony provider, Soniox and the language model are held outside the repository.
- Running cost: telephony minutes and speech and model usage per call, bounded by the spend ceiling.
- Code is copied out of a private repository into a public one: every copied file is checked for credentials, internal host names and real data before it goes in (task 1.3).
