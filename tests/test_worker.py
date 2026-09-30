import time
from pathlib import Path

import pytest

from app import worker


@pytest.fixture
def heartbeat_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "heartbeat"
    monkeypatch.setattr(worker, "HEARTBEAT_FILE", path)
    return path


def test_worker_is_unhealthy_before_its_first_round(
    heartbeat_file: Path,
) -> None:
    assert not worker.is_healthy()


def test_worker_is_healthy_after_a_recent_round(heartbeat_file: Path) -> None:
    heartbeat_file.touch()

    assert worker.is_healthy()


def test_worker_is_unhealthy_when_rounds_stop(heartbeat_file: Path) -> None:
    heartbeat_file.touch()
    later = time.time() + worker.HEARTBEAT_MAX_AGE_SECONDS + 1

    assert not worker.is_healthy(now=later)
