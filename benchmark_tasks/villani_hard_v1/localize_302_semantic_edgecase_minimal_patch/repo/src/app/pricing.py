from .math_utils import apply_credit

def invoice_total(subtotal_cents: int, credit_cents: int, fee_cents: int = 0) -> int:
    return apply_credit(subtotal_cents, credit_cents) + fee_cents
