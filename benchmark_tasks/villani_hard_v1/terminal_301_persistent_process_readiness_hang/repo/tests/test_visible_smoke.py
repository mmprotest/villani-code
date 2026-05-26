from scripts.smoke import run_smoke

def test_smoke_script_returns_healthy_status():
    assert run_smoke(port=8765) == {'status': 'ok'}
