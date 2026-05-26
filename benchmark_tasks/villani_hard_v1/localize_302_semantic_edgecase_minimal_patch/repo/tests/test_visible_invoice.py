from app.report import invoice_summary

def test_credit_cannot_increase_total():
    assert invoice_summary(1000, -200, fee_cents=50)['total_cents'] == 1050
