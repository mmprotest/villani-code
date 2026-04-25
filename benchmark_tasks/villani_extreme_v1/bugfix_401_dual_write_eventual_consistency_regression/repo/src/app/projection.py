class BalanceProjection:
    def __init__(self): self._balances={}
    def apply(self, event): self._balances[event.account_id]=self._balances.get(event.account_id,0)+event.amount
    def get(self, account_id): return self._balances.get(account_id,0)
