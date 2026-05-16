# Hack The Box Migration Plan

This file describes what would be needed to adapt this project from a CTFd-first solver into something that can operate against Hack The Box.

## Short answer

The solver swarms, sandbox, strategy layer, and coordinator logic are mostly reusable.
The part that is not reusable as-is is the platform integration layer, because the current code assumes CTFd-style behavior for:

- listing challenges
- detecting solved challenges
- downloading challenge attachments
- submitting flags
- polling new content over time

That assumption is concentrated in [backend/ctfd.py](../backend/ctfd.py), [backend/poller.py](../backend/poller.py), [backend/agents/coordinator_core.py](../backend/agents/coordinator_core.py), and the CLI/config plumbing in [backend/cli.py](../backend/cli.py) and [backend/config.py](../backend/config.py).

## What can stay

These parts should largely remain unchanged:

- challenge solving swarms
- Docker sandbox setup
- model selection and concurrency controls
- strategy scoring and stop/defer logic
- coordinator messaging and operator messaging
- cost tracking and runtime telemetry

In other words, the agent brain is reusable. The platform adapter is the main missing piece.

## What must be added

### 1. A platform abstraction

Create a generic interface for the remote CTF platform instead of hard-coding CTFd.

Minimum methods the rest of the app needs:

- list challenges
- list solved challenges
- fetch full challenge details
- download challenge files
- submit a solution
- resolve challenge identifiers by name

Suggested shape:

- `PlatformClient`
- `CTFdClient` implementation
- `HackTheBoxClient` implementation

This avoids turning the rest of the codebase into a pile of `if platform == ...` branches.

### 2. A Hack The Box data mapper

HTB does not necessarily expose the same fields as CTFd.
You will need to normalize HTB challenge data into the local format used by `metadata.yml`.

The mapper needs to decide how to translate:

- challenge name
- category / difficulty
- description
- points
- attachments / files
- hints
- solved count or solve state
- connection info / instances, if relevant

### 3. A solved-state source

The current poller assumes solved status can be queried cheaply and repeatedly.
For HTB you need to verify whether solved state is available through an API, a web session, or not at all.

If solved state is available:

- keep polling, but point it at the HTB source

If solved state is not available or is unreliable:

- use a local cache of what the agent has already solved
- optionally refresh from HTB less frequently
- treat the coordinator as a pull-based system instead of a live poller

### 4. Submission handling

The submission workflow is one of the biggest unknowns.
You need to confirm whether HTB challenge submissions can be done through:

- a public or private API
- authenticated web requests
- browser automation
- a hybrid of API plus session cookies

Once that is known, `submit_flag()` can be rewritten to return the same result categories the rest of the code expects:

- correct
- already solved
- incorrect
- unknown

### 5. Authentication and credentials

Replace the CTFd-specific settings with HTB-specific ones.

Likely settings:

- HTB base URL
- HTB API token, if available
- username and password, if a session login flow is needed
- optional session cookie or MFA-handled login path

That will also require CLI and environment variable changes so the tool no longer says `ctfd` everywhere.

### 6. Challenge sync behavior

The current coordinator assumes it can auto-pull new challenges into `challenges/`.
For HTB, decide whether the agent should:

- pull all visible challenges into local folders
- only pull selected challenges on demand
- maintain a cache of already synced challenges
- support manual import for cases where downloads are not exposed cleanly

The safest approach is usually on-demand sync plus caching.

## Unknowns that must be confirmed about HTB

Before coding, you need to verify these platform facts:

- Does HTB expose a stable API for challenge listing and submission?
- Can solved challenges be queried programmatically?
- Are challenge attachments directly downloadable?
- Do challenges have predictable IDs and categories?
- Are flags submitted per challenge, per team, or per account context?
- Is browser-session authentication required?
- Are there rate limits or anti-automation checks that affect polling?
- Are you targeting HTB CTF challenges, HTB Machines, or HTB Labs? These are not the same problem.

That last question matters a lot: this repository fits CTF-style challenge solving much better than machine/lab-style exploitation.

## Implementation order

### Phase 1: Define the platform interface

Work item:

- extract the CTF platform behavior behind a small interface
- keep the current CTFd implementation as the first backend

Outcome:

- the rest of the code stops depending directly on CTFd naming and response shapes

### Phase 2: Add HTB support behind the interface

Work item:

- implement an HTB client
- map HTB challenge metadata into local challenge folders
- implement solve detection and submission

Outcome:

- the agent can at least read and attempt HTB challenges

### Phase 3: Update the coordinator and poller

Work item:

- make the poller platform-neutral
- make challenge discovery work from the new abstraction
- make solved-state sync work from the new abstraction

Outcome:

- new challenges can be discovered without CTFd-specific assumptions

### Phase 4: Rename CLI/configuration

Work item:

- replace `ctfd-url` / `CTFD_URL` naming with platform-neutral names
- add `--platform htb` or similar
- keep CTFd as a supported backend if you still want it

Outcome:

- the tool reads like a multi-platform solver instead of a CTFd-only solver

### Phase 5: Add tests and fixtures

Work item:

- add tests for platform abstraction behavior
- add sample HTB payload fixtures
- add tests for metadata normalization and submission result mapping

Outcome:

- you can refactor without breaking the existing CTFd path

## Practical recommendation

If the goal is to support both CTFd and Hack The Box, do not rewrite the whole project.
Add a platform abstraction first, keep CTFd working, then slot HTB in as a second backend.

If the goal is to support only HTB, then you can simplify more aggressively, but you will still want the abstraction boundary so the solver side stays isolated from platform details.

## Next things to do

1. Confirm whether the target is HTB CTF challenges, HTB Machines, or HTB Labs.
2. Find the HTB auth and challenge APIs you want to rely on.
3. Define the `PlatformClient` interface and migrate the current CTFd code behind it.
4. Implement HTB challenge listing, attachment download, solve detection, and submission.
5. Rename CLI/config values to be platform-neutral.
6. Add fixtures and tests for the new backend.