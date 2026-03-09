from pathlib import Path

from villani_code.cli import _resolve_preset
from villani_code.state import Runner


class DummyClient:
    def create_message(self, _payload, stream):
        assert stream is False
        return {"id": "1", "role": "assistant", "content": [{"type": "text", "text": "done"}]}


def test_runner_defaults_to_local_safe_small_model(tmp_path: Path) -> None:
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)
    assert runner.small_model is True
    assert runner.plan_mode == "strict"
    assert runner.preset == "local-safe"


def test_preset_resolution_defaults_and_opt_out() -> None:
    small, plan_mode, repairs = _resolve_preset("local-safe")
    assert (small, plan_mode, repairs) == (True, "strict", 1)

    small_cloud, plan_cloud, repairs_cloud = _resolve_preset("cloud-power")
    assert (small_cloud, plan_cloud, repairs_cloud) == (False, "auto", 2)

    small_out, _plan, _repairs = _resolve_preset("local-safe", no_small_model=True)
    assert small_out is False
