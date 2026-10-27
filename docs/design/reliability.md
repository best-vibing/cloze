# 상세 설계서 — 신뢰성(재시도·시간 설정·멱등성)

- 상위 문서: [아키텍처 정의서](../architecture.md) 6장
- 관련 결정: [ADR-001 작업 큐](../decisions/ADR-001-task-queue-taskiq.md)

## 1. 실패 종류별 담당

| 상황 | 담당 | 동작 |
|---|---|---|
| 워커가 처리 중 죽음(배포, 메모리 초과) | RedisStreamBroker의 ack·재배달 | ack가 없으면 `idle_timeout` 후 다른 워커가 넘겨받아 재실행 [확인] |
| Gemini 일시 오류, 네트워크 끊김 | `SimpleRetryMiddleware` | 작업이 예외를 던지면 같은 인자로 다시 보냄. `retry_on_error=True` 라벨이 붙은 작업에만 적용 [확인]. 최대 2회 |
| Gemini 응답이 검증 실패 | 파이프라인 내부 | 오류를 붙여 1회 재시도 → 규칙 기반 폴백. **예외를 던지지 않는다**(던지면 미들웨어가 작업 전체를 다시 돌려 Gemini 비용이 두 배) |
| OCR 인식 실패, 잘못된 파일 | 파이프라인 내부 | 상태 `failed` + 사유 기록. 재시도할 의미가 없으므로 예외를 던지지 않음 |
| 자료 분석 실패 | 파이프라인 내부 | 기본 방식으로 진행하고 사유 기록([선택·품질 게이트](selection-and-quality-gate.md) 1절). 예외를 던지지 않음 |
| 품질 게이트 미달 | 파이프라인 내부 | 형식 검증 실패와 같은 경로([선택·품질 게이트](selection-and-quality-gate.md) 2.3). 예외를 던지지 않음 |
| Redis 재시작 | Redis AOF | 대기 중 작업 보존 |

정리하면 **작업이 예외를 던지는 경우는 "다시 하면 될 수도 있는" 일시적 오류뿐**이다.

참고: `SimpleRetryMiddleware`는 "Redis가 죽었을 때 재시도"가 아니라 "작업 실행 중 예외가 났을 때 재시도"다. 워커·Redis 장애 대응은 위 표의 Stream ack와 AOF가 맡는다.

## 2. 시간 설정 (초기값)

| 설정 | 초기값 | 근거 |
|---|---|---|
| 업로드당 페이지 상한 | 30쪽 | DESIGN D1 |
| 작업 제한 시간 (Taskiq `timeout` 라벨) | 8분 | 30쪽 처리 시간을 측정한 뒤 조정 |
| `idle_timeout` (RedisStreamBroker) | 15분 | 기본값은 `600000`(밀리초, 10분). 이 시간 동안 ack가 없으면 `XAUTOCLAIM`으로 다른 워커가 넘겨받는다 [확인] |
| Gemini 호출당 타임아웃 | 측정 후 결정 | R4 |

**규칙: 작업 제한 시간 < `idle_timeout`.** `idle_timeout`이 지나면 워커가 살아서 아직 처리 중이어도 작업이 다른 워커에 다시 넘어가 두 번 실행된다(장시간 작업에서 실제로 보고된 사례) [확인]. 30쪽 처리가 8분을 넘는 것으로 측정되면 작업을 페이지 묶음 단위로 쪼갠다.

## 3. 멱등성

Stream 방식은 "최소 한 번 실행"이므로 같은 작업이 두 번 돌 수 있다. 두 번 돌아도 결과가 같게 만든다.

1. 작업 시작 시 Postgres에서 빈칸 세트 상태를 확인하고, 이미 `done`이면 바로 종료(ack).
2. 빈칸·정답 저장은 빈칸 세트 ID 기준 upsert(또는 삭제 후 삽입을 한 트랜잭션으로).
3. 이미지 키를 `blank_sets/{세트 ID}/page_{번호}.png`처럼 고정해서 덮어써도 무해하게.
4. 모든 저장이 끝난 뒤에 상태를 `done`으로 바꾸고, 그다음 ack.

중복 실행 시 남는 손해는 Gemini 호출 비용뿐이고, 드문 경우라 감수한다.

## 4. 결과 백엔드

`RedisAsyncResultBackend`는 디버깅용으로만 쓰고 결과 만료 시간을 설정해 Redis에 결과가 계속 쌓이지 않게 한다(설정 파라미터 이름은 구현 시 확인 [미확인]). 상태 조회 API는 결과 백엔드를 읽지 않는다.

## 5. 남은 확인 사항

| 항목 | 상태 |
|---|---|
| `RedisAsyncResultBackend` 만료 설정 방법 | [미확인] |
| 30쪽 처리 시간 실측 → 작업 제한 시간·`idle_timeout` 조정 | 로드맵 5단계에서 측정 |

## 출처

- Taskiq 미들웨어(SimpleRetryMiddleware): https://taskiq-python.github.io/available-components/middlewares.html
- RedisStreamBroker 파라미터(`idle_timeout` 기본값, `XAUTOCLAIM`): https://github.com/taskiq-python/taskiq-redis/blob/master/taskiq_redis/redis_broker.py
- RedisStreamBroker 장시간 작업 재배달 이슈: https://github.com/taskiq-python/taskiq-redis/issues/101
