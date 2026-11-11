"""??? ????? ???? ?? ??? ??."""

import socket
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from google import genai


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """??? ??? .env? ???? ?? SDK ? ???? ??? ????."""
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
