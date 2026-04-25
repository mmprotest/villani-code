class LegacyStore:
    def __init__(self): self._balances={}; self._applied_request_ids=set()
    def apply_adjustment(self, account_id, amount, request_id):
        if request_id in self._applied_request_ids: return False
        self._balances[account_id]=self._balances.get(account_id,0)+amount
        self._applied_request_ids.add(request_id)
        return True
    def get_balance(self, account_id): return self._balances.get(account_id,0)
