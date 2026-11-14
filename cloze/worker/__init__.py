"""Taskiq 브로커와 작업. 실행: uv run taskiq worker cloze.worker:broker --max-async-tasks 1"""

import logging

from taskiq import SimpleRetryMiddleware
from taskiq_redis import RedisStreamBroker

from cloze.config import load_settings

logger = logging.getLogger(__name__)
_settings = load_settings()

# ADR-001: ack를 지원하는 Stream 브로커만 쓴다. ack는 기본값(when_saved)대로 실행이 끝난 뒤 보낸다.
# 재시도 횟수는 run_count 상한(4)보다 크게 둬서 판단을 DB 쪽이 하게 한다(신뢰성 설계 3절).
broker = RedisStreamBroker(
    _settings.redis_url, idle_timeout=_settings.queue_idle_timeout_ms
).with_middlewares(SimpleRetryMiddleware(default_retry_count=5))


# 이름을 고정한다. 기본 이름은 모듈 경로라 파일을 옮기면 바뀌고, 큐에 남은 옛 이름의 작업은
# 워커가 모르는 작업으로 ack되어 사라진다(재배달 실험에서 확인).
@broker.task(task_name="process_set", timeout=_settings.task_timeout_s, retry_on_error=True)
async def process_set(set_id: int) -> None:
    # 빈 작업. 실제 파이프라인은 D6에서 넣는다.
    logger.info("세트 %d 받음", set_id)
    logger.info("세트 %d 끝", set_id)
