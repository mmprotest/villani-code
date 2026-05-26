from scripts.smoke import run_smoke

def test_smoke_script_can_run_twice_without_port_leak():
    assert run_smoke(port=8766) == {'status': 'ok'}
    assert run_smoke(port=8766) == {'status': 'ok'}
