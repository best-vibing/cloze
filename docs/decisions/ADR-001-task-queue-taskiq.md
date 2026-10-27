# ADR-001: 작업 큐 — Taskiq + RedisStreamBroker 채택

## 상태
채택됨 (2026-10-27)

## 컨텍스트

PDF 한 건을 처리하는 데 수 초에서 수 분이 걸린다(렌더링, 좌표 추출, 페이지별 Gemini 호출, 가린 이미지 생성). 이 처리에 필요한 조건은 다음과 같다.

1. HTTP 요청 하나가 처리 시간 내내 붙잡혀 있으면 안 된다. 업로드는 작업 ID만 받고 브라우저가 상태를 폴링한다.
2. 배포나 메모리 초과로 워커가 재시작돼도 실행 중이던 작업이 사라지면 안 된다.
3. 처리 시간의 상당 부분이 Gemini 응답 대기(I/O)이므로 asyncio로 동시에 기다리는 편이 유리하다.
4. 팀원 일부가 Windows에서 개발한다.

## 검토한 선택지

| 선택지 | 장점 | 단점 | 결론 |
|---|---|---|---|
| 요청 안에서 동기 처리 | 가장 단순 | 프록시 타임아웃에 걸리고, 연결이 끊기면 결과와 비용을 잃는다 | 조건 1 불충족 → 탈락 |
| FastAPI `BackgroundTasks` | 추가 구성 없음 | API 프로세스 메모리에서 돌아 재시작 시 유실. PDF 파서를 격리할 수 없다 | 조건 2 불충족 → 탈락 |
| RQ | 단순 | 동기 함수 기반. 기본 워커가 `fork`에 의존해 Windows에서 돌지 않는 것으로 알려짐 [미확인] | 조건 3·4 → 탈락 |
| Celery | 기능이 가장 많음 | 설정이 많고 async 기반이 아님. Windows 공식 미지원으로 알려짐 [미확인] | 이 규모에 과함 → 탈락 |
| arq | Redis + asyncio에 정확히 맞음. 워커 종료 시 작업이 큐에 남음 | 2025-10-18부터 유지보수 전용 모드. 치명적 보안 문제만 고치고 일반 버그 수정은 기대하지 말라고 명시 [확인] | 코드가 없는 지금 바꾸는 비용이 가장 작음 → 탈락 |
| **Taskiq** | asyncio 기반, FastAPI 연동 가이드 제공, 활발히 릴리스(0.13.0, 2026-09-26) [확인] | 0.x 버전이라 업그레이드 때 API가 바뀔 수 있다 | **채택** |

## 결정

Taskiq를 쓰고, Redis 브로커는 **`RedisStreamBroker`만** 쓴다.

taskiq-redis의 브로커 세 종류 가운데 `ListQueueBroker`와 `PubSubBroker`는 README에 "acknowledgement를 지원하지 않으며 처리 중 워커가 죽으면 메시지가 유실된다"고 명시돼 있다 [확인]. 이 둘을 쓰면 조건 2가 깨진다.

`RedisStreamBroker`는 Redis 스트림의 컨슈머 그룹을 쓴다. 워커가 작업을 읽으면 ack를 보내기 전까지 대기 목록(PEL)에 남고, `idle_timeout`(기본 `600000`ms = 10분) 동안 ack가 없으면 `XAUTOCLAIM`으로 다른 워커가 넘겨받는다 [확인].

## 결과

- **작업이 두 번 이상 실행될 수 있다(at-least-once).** 저장을 멱등하게 만들어야 한다([신뢰성 설계](../design/reliability.md) 3절).
- **작업 제한 시간 < `idle_timeout`** 규칙이 생긴다. 어기면 살아서 처리 중인 작업이 다른 워커에서 한 번 더 실행된다([신뢰성 설계](../design/reliability.md) 2절).
- 작업 상태의 기준은 Postgres에 둔다. 결과 백엔드는 디버깅용 보조로만 쓴다.
- 0.x 버전이므로 Taskiq와 taskiq-redis 버전을 고정하고, 올릴 때는 변경 기록을 확인한다.

## 확인 필요

- Taskiq가 ack를 작업 실행 **후에** 보내는지, 그 시점을 설정으로 바꿀 수 있는지. 실행 전에 보낸다면 Stream을 써도 유실된다.
- 위 동작은 문서와 소스로 확인했을 뿐이다. 작업 중 워커를 강제 종료해 재실행되는지 실험으로 확인한다.

## 출처

- arq 유지보수 전용 모드: https://github.com/python-arq/arq/issues/510
- taskiq 릴리스 기록: https://pypi.org/project/taskiq/#history
- taskiq-redis 브로커별 ack 지원: https://github.com/taskiq-python/taskiq-redis
- `RedisStreamBroker` 기본값과 `XAUTOCLAIM`: https://github.com/taskiq-python/taskiq-redis/blob/master/taskiq_redis/redis_broker.py
