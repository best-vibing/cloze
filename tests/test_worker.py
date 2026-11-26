"""브로커 설정 확인. Redis 없이 객체 속성만 본다(브로커는 startup 전까지 접속하지 않는다)."""

from cloze.config import load_settings
from cloze.worker import AUTOCLAIM_LOCK_TIMEOUT_S, broker


def test_task_name_is_fixed() -> None:
    # 이름이 바뀌면 큐에 남은 옛 이름의 작업이 경고만 남기고 ack되어 사라진다.
    assert broker.find_task("process_set") is not None


def test_broker_takes_one_message_at_a_time() -> None:
    assert broker.count == 1  # xread_count
    assert broker.unacknowledged_batch_size == 1
    assert broker.unacknowledged_lock_timeout == AUTOCLAIM_LOCK_TIMEOUT_S


def test_idle_timeout_comes_from_settings() -> None:
    settings = load_settings()
    assert broker.idle_timeout == settings.queue_idle_timeout_ms
    assert settings.task_timeout_s * 1000 < broker.idle_timeout  # 신뢰성 설계 2절
