# fake-payer

A **fictional dental insurance phone line**, for rehearsing automated calls to payers.

You call a number. After a couple of rings a made-up insurance representative ("Adrian") answers, listens, and gives coverage answers for made-up members. The answers come from a fixed data file, so the same question always gets the same answer and a test can check exactly what a caller was told.

**This is a test fixture, not a product.** It uses synthetic data only. It does not imitate any real insurer: no real payer's name, number, plan terms or member data belongs in this repository. A check (`python -m fake_payer.real_data_check`, run in CI) guards against real data creeping in.

## How a call goes

1. The line rings (`RING_COUNT` rings of ringback tone), waits a moment (`ANSWER_DELAY_SECONDS`), then greets the caller and says the call is fictional and recorded.
2. The representative asks for what it needs to find the patient (member number, date of birth and name, group number, NPIs, tax ID digits). Any patient the caller names is accepted as an ordinary active member; the named fixture members keep their scenarios (see below). Name pronunciation need not be exact.
3. It answers **only what is asked**, in a sentence or two, and does not ask "anything else?" after each answer. Every fact comes from a lookup in the fixture, done *before* it starts speaking, so there are no silent gaps that make a caller think the turn is over.
4. It **keeps the floor**: while it is audibly speaking it ignores the caller talking over it (`KEEP_THE_FLOOR=0` lets the caller cut it off).
5. It waits as long as the caller has questions. There is no call-length limit by default.
6. It hangs up only after the caller has said goodbye. "Thank you" or "that is all" does not end the call: it stays on the line, gives its name, ID and the call reference if the caller has not had them, and waits. When the caller says goodbye it says goodbye back and hangs up (the end-call tool refuses until the caller's last words contain a goodbye). After either side hangs up the line plays a three-second dial tone.

## What it knows

Everything is in [`fake_payer/fixture.yaml`](fake_payer/fixture.yaml) (all invented):

- **Plans**: `GOLD` (annual maximum, deductible, tiered percentages and waiting periods, orthodontics with a lifetime maximum and age limit) and `SILVER` (no orthodontic benefit).
- **Members** (each has a `note` saying what it is for): `M100001` Pat Sample, the ordinary case; `M100002` Robin Example, frequency limits used up; `M100003` Casey Placeholder, terminated coverage; `M100004`, annual maximum nearly used.
- **Procedures**: a handful of codes with category and frequency limit. A caller does not need to give a code; "what is covered for fillings" works.
- **`standing_answers`**: answers given whoever the patient is (employee ID, fee schedule, once-per-lifetime, missing tooth clause, prior carrier, treatment in progress).
- **`benefit_answers`**: a plausible answer for every question in the platform's insurance benefit catalog, found by `lookup_benefit_answer`.

Adding a member or an answer needs no code change; edit the fixture. Tests check the fixture's shape, and `grounding.py` checks that what the representative said matches what the lookup returned.

## Running it

Python 3.11+.

```
pip install -e ".[dev]"
pytest
python -m fake_payer          # reads configuration from the environment; a .env file in the working directory fills in anything not set
```

It will not start if a required variable is missing, and no credential is ever printed or logged.

Required: `SONIOX_API_KEY`, `ANTHROPIC_API_KEY`, `FAKE_PAYER_NUMBER`, `ANSWER_KEY` (a secret of 16+ characters that goes in the answer URL).

Optional (defaults in parentheses; see [`.env.example`](.env.example) and `fake_payer/config.py`):

| Variable | Meaning |
| --- | --- |
| `MAX_CONCURRENT_CALLS` (3), `MAX_CALLS_PER_NUMBER_PER_HOUR` (120) | Volume limits |
| `MAX_CALL_SECONDS` (0) | Call-length limit; 0 means none |
| `DAILY_SPEND_CEILING_USD` (5), `COST_PER_MINUTE_USD` (0.15) | Spend estimate ceiling |
| `RING_COUNT` (2), `ANSWER_DELAY_SECONDS` (2), `REPLY_DELAY_SECONDS` (2) | Pacing: rings before answering, pause after the last ring, least time from the caller finishing to the first sound of the reply |
| `KEEP_THE_FLOOR` (1) | Carry on speaking when talked over |
| `TTS_VOICE` (Adrian), `SONIOX_STT_MODEL`, `SONIOX_TTS_MODEL` | Speech settings |
| `LISTEN_HOST` (127.0.0.1), `PORT` (8080) | Where it listens |
| `CALL_LOG_DIR` (calls), `LOG_RETENTION_DAYS` (7), `AUDIO_CACHE_DIR` | Call log and cached spoken refusals |

## Telephony

[Vonage](https://www.vonage.com) delivers the number. Vonage requests `GET|POST /answer?key=<ANSWER_KEY>` and the reply is an NCCO that connects the call to this program's websocket (audio/l16, 8 kHz). Only requests carrying the key are answered; every other path is refused. There are no pages and no user interface.

A Vonage number links to **one application**. If another app (for example a voice-agent app in another product) is linked to the number, calls stop reaching this line; relink the number to this line's application.

Speech is [Soniox](https://soniox.com) in and out; the conversation is a language model (Anthropic) with two tools, `lookup_coverage` / `lookup_benefit_answer` and `end_call`.

## Call log

Each call is written to a JSON file in `CALL_LOG_DIR` (the turns, how it ended, duration, caller's last four digits and the call reference), and files older than `LOG_RETENTION_DAYS` are removed. The call reference the representative gives is derived from the call, so a caller's saved reference can be matched to its log entry.

## Deploying

The `Dockerfile` builds an image that keeps the call log and cached audio in the `/data` volume and listens on port 8080 behind a host reverse proxy. The hosting that runs it (VM, DNS, secrets, number linking and redeploys) is scripted in a separate infrastructure repository, not here. Redeploy only when no call is in progress.

## Not in this repository

Credentials, the telephone number, real insurer names and real data. Configuration comes from the environment; only an example with fictional values is kept here.

## Specification

The original design is in [`openspec/changes/add-fake-payer-phone-line/`](openspec/changes/add-fake-payer-phone-line/) (proposal, design, specs, tasks). Later behaviour (ringing, keeping the floor, ending the call, the dial tone, answers for the whole benefit catalog, no time limit) was added after it, so where they differ, the code and this README are current. A phone menu, hold time, refusals, transfers and conflicting answers are still to come.
