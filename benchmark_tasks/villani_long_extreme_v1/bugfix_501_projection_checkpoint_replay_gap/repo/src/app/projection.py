from .models import Event
class BalanceProjector:
    def __init__(self, projection_store, checkpoint_store): self.projection_store=projection_store; self.checkpoint_store=checkpoint_store
    def apply_event(self, event:Event):
        if self.projection_store.was_applied(event.seq): return
        current=self.projection_store.get_balance(event.account)
        if event.kind=="credit": self.projection_store.set_balance(event.account, current+event.amount)
        elif event.kind=="debit": self.projection_store.set_balance(event.account, current-event.amount)
        else: raise ValueError(event.kind)
        self.projection_store.mark_applied(event.seq); self.checkpoint_store.save(event.seq)
    def catch_up(self, event_log):
        # BUG: assumes checkpoint implies projection state is complete.
        for event in event_log.read_from(self.checkpoint_store.load()): self.apply_event(event)
