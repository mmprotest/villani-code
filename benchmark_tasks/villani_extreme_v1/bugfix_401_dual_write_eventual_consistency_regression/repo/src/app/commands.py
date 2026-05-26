from .models import AdjustmentApplied, AdjustmentCommand

def build_dedupe_key(command: AdjustmentCommand) -> str:
    return f"{command.request_id}:{command.received_at_ms}"

class AdjustmentService:
    def __init__(self, legacy_store, event_log, projection):
        self._legacy_store=legacy_store; self._event_log=event_log; self._projection=projection
    def apply(self, command: AdjustmentCommand) -> None:
        applied=self._legacy_store.apply_adjustment(command.account_id, command.amount, command.request_id)
        if not applied:
            event=AdjustmentApplied(command.account_id, command.amount, command.request_id, build_dedupe_key(command))
            self._event_log.append(event)
            self._projection.apply(event)
            return
        event=AdjustmentApplied(command.account_id, command.amount, command.request_id, build_dedupe_key(command))
        self._event_log.append(event)
        self._projection.apply(event)
