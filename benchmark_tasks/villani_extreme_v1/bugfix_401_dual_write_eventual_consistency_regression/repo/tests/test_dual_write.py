from app.commands import AdjustmentService
from app.events import EventLog
from app.models import AdjustmentCommand
from app.projection import BalanceProjection
from app.read_model import AccountReader
from app.repository import LegacyStore

def build_service():
    legacy=LegacyStore(); log=EventLog(); projection=BalanceProjection(); return AdjustmentService(legacy,log,projection), legacy, log, AccountReader(projection)

def test_retry_does_not_double_apply_read_model():
    service, legacy, log, reader = build_service()
    service.apply(AdjustmentCommand("acct-1",50,"req-1",1000))
    service.apply(AdjustmentCommand("acct-1",50,"req-1",1001))
    assert legacy.get_balance("acct-1") == 50
    assert reader.current_balance("acct-1") == 50

def test_dedupe_key_is_stable_for_logical_retry():
    service, _, log, _ = build_service()
    service.apply(AdjustmentCommand("acct-1",50,"req-1",1000))
    service.apply(AdjustmentCommand("acct-1",50,"req-1",9999))
    assert [e.dedupe_key for e in log.all()] == ["req-1"]

def test_distinct_requests_still_append_distinct_events():
    service, legacy, log, reader = build_service()
    service.apply(AdjustmentCommand("acct-1",50,"req-1",1000))
    service.apply(AdjustmentCommand("acct-1",-20,"req-2",1001))
    assert legacy.get_balance("acct-1") == 30
    assert reader.current_balance("acct-1") == 30
    assert [e.request_id for e in log.all()] == ["req-1","req-2"]
