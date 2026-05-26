from .pricing import invoice_total

def invoice_summary(subtotal_cents: int, credit_cents: int, fee_cents: int = 0) -> dict:
    total = invoice_total(subtotal_cents, credit_cents, fee_cents)
    return {'subtotal_cents': subtotal_cents, 'credit_cents': credit_cents, 'fee_cents': fee_cents, 'total_cents': total}
