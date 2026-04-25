from app.pricing import invoice_total

def test_positive_credit_reduces_total():
    assert invoice_total(1000, 200, fee_cents=50) == 850

def test_credit_cannot_drive_subtotal_below_zero():
    assert invoice_total(100, 200, fee_cents=0) == 0
