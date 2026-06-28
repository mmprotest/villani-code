# @mmprotest/pi-villani
Install with:
```bash
pi install npm:@mmprotest/pi-villani
```
Provides `/villani <task>`, `/villani-abort`, and `/villani-confirm-test`.

Runtime version: `v0.1.2`.

## Abort semantics

When `/villani-abort` is invoked, the extension first aborts the Pi model proxy signal and sends a bridge `{ "type": "abort" }` command for the active run. Pending Villani approval requests are denied by the bridge immediately so approved writes do not continue after abort. The extension waits a short grace period for `run_aborted`; if it is not observed, the bridge subprocess is killed. Active child commands launched by the runtime may continue until that subprocess is killed, so abort guarantees no orphan bridge process rather than instant termination of every child process.
