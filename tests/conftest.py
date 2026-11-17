"""Gemini 테스트에서 명시적으로 사용하는 오프라인 환경."""

import socket
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from google import genai


@pytest.fixture
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """설정과 작업 폴더를 격리하고 실제 SDK 및 네트워크 호출을 막는다."""
    monkeypatch.chdir(tmp_path)
    for suffix in (
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GEMINI_TIMEOUT_S",
        "GEMINI_MAX_CONCURRENCY",
        "SAMPLES_DIR",
    ):
        monkeypatch.delenv(f"CLOZE_{suffix}", raising=False)
    blocked = MagicMock(side_effect=AssertionError("Unexpected network or SDK access"))
    monkeypatch.setattr(genai, "Client", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
