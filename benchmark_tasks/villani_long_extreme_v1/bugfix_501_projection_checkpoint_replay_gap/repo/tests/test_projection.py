from app.models import Event
from app.event_log import EventLog
from app.checkpoint import CheckpointStore
from app.projection_store import ProjectionStore
from app.rebuild import resume_projection
def seed():
    return EventLog([Event(1,"credit","a",5),Event(2,"credit","b",7),Event(3,"debit","a",2),Event(4,"credit","a",10)])
def test_resume_after_checkpoint_rebuilds_missing_projection_rows():
    log=seed(); ckpt=CheckpointStore(); ckpt.save(4); store=ProjectionStore(); store.set_balance("a",13); store.applied.update({1,3,4}); resume_projection(log,store,ckpt); assert store.get_balance("b")==7
def test_idempotent_replay_does_not_double_apply_existing_events():
    log=seed(); ckpt=CheckpointStore(); store=ProjectionStore(); resume_projection(log,store,ckpt); resume_projection(log,store,ckpt); assert store.rows=={"a":13,"b":7}
def test_resume_keeps_checkpoint_monotonic_when_repairing_missing_rows():
    log=seed(); ckpt=CheckpointStore(); ckpt.save(4); store=ProjectionStore(); store.set_balance("a",13); store.applied.update({1,3,4}); resume_projection(log,store,ckpt); assert ckpt.load()==4
