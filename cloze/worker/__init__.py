"""Taskiq 브로커와 작업. 실행: uv run taskiq worker cloze.worker:broker --max-async-tasks 1"""

import logging

from taskiq import SimpleRetryMiddleware
from taskiq_redis import RedisStreamBroker

from cloze.config import load_settings

logger = logging.getLogger(__name__)
_settings = load_settings()

# 회수 잠금(XAUTOCLAIM)을 잡은 채 워커가 강제 종료돼도 이 시간 뒤에는 풀린다.
# 기본값 None이면 풀리지 않아 다른 워커가 모두 잠금을 기다리며 멈춘다.
AUTOCLAIM_LOCK_TIMEOUT_S = 30

# ADR-001: ack를 지원하는 Stream 브로커만 쓴다. ack는 기본값(when_saved)대로 실행이 끝난 뒤 보낸다.
# 재시도 횟수는 run_count 상한(4)보다 크게 둬서 판단을 DB 쪽이 하게 한다(신뢰성 설계 3절).
#
# 한 번에 하나만 가져온다(xread_count·unacknowledged_batch_size = 1). 기본값 100이면
# --max-async-tasks 1이어도 쌓인 작업을 한꺼번에 이 컨슈머의 대기 목록(PEL)으로 가져와,
# 시작도 안 한 작업의 idle 시계가 그때부터 간다. 8분짜리 3개면 세 번째는 15분을 넘겨
# 다른 워커가 회수하고, 원래 워커도 실행해 두 번 돈다(신뢰성 설계 2절, #91 리뷰).
broker = RedisStreamBroker(
    _settings.redis_url,
    idle_timeout=_settings.queue_idle_timeout_ms,
    xread_count=1,
    unacknowledged_batch_size=1,
    unacknowledged_lock_timeout=AUTOCLAIM_LOCK_TIMEOUT_S,
).with_middlewares(SimpleRetryMiddleware(default_retry_count=5))


# 이름을 고정한다. 기본 이름은 모듈 경로라 파일을 옮기면 바뀌고, 큐에 남은 옛 이름의 작업은
# 워커가 모르는 작업으로 ack되어 사라진다(재배달 실험에서 확인).
@broker.task(task_name="process_set", timeout=_settings.task_timeout_s, retry_on_error=True)
async def process_set(set_id: str) -> None:
    # 세트 ID는 UUID 문자열이다(ADR-006). 빈 작업. 실제 파이프라인은 #52에서 넣는다.
    logger.info("세트 %s 받음", set_id)
    logger.info("세트 %s 끝", set_id)
