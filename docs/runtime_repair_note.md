# Runtime repair and verification note

## What was broken

- A failed verification could enter a path called repair without proving that a real second patch cycle happened.
- Verification after failure could rerun broad or repeated commands even when there was no new edit.
- Low-authority files such as package init or config/build files could still become lazy fallbacks under uncertainty.
- Runtime telemetry was not reliable enough to explain whether repair, retry, recovery, or no-progress termination actually happened.

## What was fixed

- Repair is now treated as a real runtime phase with explicit patch-cycle bookkeeping.
- A repair retry counts only when a post-failure repair actually produces a new edit.
- Verification now prefers targeted commands first and blocks rerunning the same verifier command for the same edit generation.
- General edit authority now rejects generated/runtime-artifact files by default and requires runtime evidence before editing low-authority config/build/package files.
- Runtime telemetry now comes from exact emitted events and is persisted as structured runtime events.

## How repair now works

1. Initial validation fails.
2. The runtime classifies the failure and enters bounded repair mode.
3. Each repair branch must either produce a real patch cycle or emit an explicit no-patch reason.
4. If a repair branch edits code, targeted verification runs first.
5. Broader verification only runs when targeted verification passes and escalation policy requires it.
6. Recovery metrics are derived from the emitted repair and validation events.

## How targeted verification now works

- The validation planner already prefers narrow checks when targets can be derived.
- The runtime now records verifier commands exactly and treats them as tied to an edit generation.
- If the same verifier command is requested again without an intervening edit, the runtime stops that rerun and surfaces a no-progress style failure instead of burning more time.

## How no-progress termination now works

- Repair branches that produce no patch emit an explicit no-patch event and do not count as real retries.
- Duplicate verifier runs for the same edit generation are blocked.
- Environment/tool failures are classified separately so the runtime does not thrash source files.

## How telemetry is sourced now

- Structured runtime events are written to `.villani_code/runtime_events.jsonl`.
- Telemetry fields such as patch attempts, retries after failure, recovery success, first-pass success, verifier commands run, first edited file, branch count, selected branch, no-patch reason, termination reason, and time-to-first-edit/verify are derived from those events rather than reconstructed afterward.
