"""재배달 실험. README "큐 (Redis·워커)" 절의 절차를 따른다.

워커:  uv run taskiq worker cloze.worker:broker scripts.redelivery_experiment --max-async-tasks 1
등록:  uv run python -m scripts.redelivery_experiment <작업 이름>
"""

import asyncio
import logging
import sys

from cloze.worker import broker

logger = logging.getLogger(__name__)


@broker.task(task_name="redelivery_slow", retry_on_error=False)
async def slow(name: str) -> None:
    # 빈 작업은 바로 끝나서 "처리 중에 죽이기"를 할 수 없다.
    logger.info("%s 받음", name)
    await asyncio.sleep(20)
    logger.info("%s 끝", name)


async def _send(name: str) -> None:
    await broker.startup()
    await slow.kiq(name)
    await broker.shutdown()


if __name__ == "__main__":
    asyncio.run(_send(sys.argv[1]))
