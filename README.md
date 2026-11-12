# fake-payer

A **fictional dental insurance phone line**, for rehearsing automated calls to payers.

You call a number. A made-up insurance provider answers, listens, and gives coverage answers for made-up members. The answers come from a fixed data file, so the same question always gets the same answer and a test can check exactly what a caller was told.

**This is a test fixture, not a product.** It uses synthetic data only. It does not imitate any real insurer: no real payer's name, number, plan terms or member data belongs in this repository.

## Status

Design stage. The first change is specified in [`openspec/changes/add-fake-payer-phone-line/`](openspec/changes/add-fake-payer-phone-line/):

- [`proposal.md`](openspec/changes/add-fake-payer-phone-line/proposal.md): why, and what is in the first cut
- [`design.md`](openspec/changes/add-fake-payer-phone-line/design.md): how it is built, and what comes later
- [`specs/`](openspec/changes/add-fake-payer-phone-line/specs/): the behavior it must have
- [`tasks.md`](openspec/changes/add-fake-payer-phone-line/tasks.md): the build steps

## What it will be

- One cooperative representative, answering coverage questions about fictional members.
- Real-time speech in and out with [Soniox](https://soniox.com) (voice "Adrian"), a language model for the conversation, and a telephone number delivered by Vonage.
- Anyone with the number may call, so it limits simultaneous calls, calls per number, call length and daily spend, and says on every call that it is fictional and recorded.
- No user interface and no web pages. Each call is written to a local file; a command reads them back.

A phone menu, hold time, refusals, transfers and conflicting answers come in a later change.

## Not in this repository

Credentials, the telephone number, real insurer names and real data. Configuration comes from the environment; only an example with fictional values is kept here.
