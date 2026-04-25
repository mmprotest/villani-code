class AccountReader:
    def __init__(self, projection): self._projection=projection
    def current_balance(self, account_id): return self._projection.get(account_id)
