# Benchmark localization and bounded repair note

This runner upgrade adds four production-focused benchmark behaviors:

1. **Benchmark localization pack**
   - Benchmark runs now always inject compact structured repository context, even when `--small-model` is off.
   - The pack includes a repo structure summary, likely source/test roots, expected task files, and ranked candidate files with reasons.

2. **Bounded repair workflow**
   - Failed verification now enters an explicit repair phase instead of a loose retry.
   - Repair inherits the benchmark contract, blocked paths, expected/support files, localization pack, touched files, and recent verification history.

3. **Tiny branch-on-failure**
   - After a failed first validation, repair considers at most two bounded branches.
   - Branches are evaluated by targeted verification first, then broadened verification only when policy requires it.

4. **Environment / harness classification**
   - Validation now distinguishes likely environment failures such as missing `make`, src-layout import resolution problems, shell incompatibilities, and missing command runners.
   - For src-layout Python verification, the runner can retry validation with a controlled `PYTHONPATH=src` mitigation instead of thrashing source files.
