from __future__ import annotations

import os

import pytest
import requests


@pytest.mark.skipif(
    os.environ.get("RUN_OLLAMA_GENERATE_SMOKE") != "1",
    reason="opt-in smoke test",
)
def test_ollama_generate_endpoint_with_qwen3_3b() -> None:
    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": "qwen3:4b",
            "prompt": "Why is the sky blue?",
            "stream": False,
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    assert data.get("model") == "qwen3:3b"
    assert isinstance(data.get("response"), str)
    assert bool(data["response"].strip())

# test_ollama_generate_endpoint_with_qwen3_3b()
# Run this test with the following command:
# RUN_OLLAMA_GENERATE_SMOKE=1 pytest tests/test_ollama_generate_smoke.py -v