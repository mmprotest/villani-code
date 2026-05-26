from app.service import record_many

def test_parallel_recording_is_lossless():
    assert record_many('message.sent', 400, workers=16) == 400
