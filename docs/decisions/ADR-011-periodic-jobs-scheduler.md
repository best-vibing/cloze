# ADR-011: 주기 작업 — Taskiq 스케줄러 한 개

## 상태
채택됨 (2026-11-11). [ADR-007](ADR-007-deploy-demo-machine-tailscale.md)의 Compose 구성(`api`·`worker`·`redis`·`postgres`)에 `scheduler`를 더한다.

## 관련 드라이버
- **D3**(작업이 사라지면 안 된다): Redis가 쪽지를 잃었거나 등록 직전에 API가 죽은 세트는 정리 작업이 다시 등록해야 끝 상태에 도착한다([신뢰성 설계](../design/reliability.md) 1.1절 4번).
- **D7**(4명, 6주): 새 도구를 들이지 않고 이미 쓰는 Taskiq 기능으로 해결한다.

## 컨텍스트

정리 작업([#30](https://github.com/best-vibing/cloze/issues/30))은 주기적으로 돌아야 한다. 하는 일은 셋이다.

- 삭제 표시된 문서의 데이터 지우기
- DB에 행이 없는 파일 지우기
- `queued`·`running`인 채로 30분 넘게 멈춘 세트 다시 등록

멈춘 세트는 두 경우로 생기며, 정리 작업이 필요한 것은 둘째뿐이다.

| 경우 | 쪽지 | 누가 끝 상태로 보내나 |
|---|---|---|
| 처리 중에 워커가 죽음 | Redis에 남음(ack 전) | Redis가 `idle_timeout`([신뢰성 설계](../design/reliability.md) 2장) 뒤 다른 워커에게 다시 배달. 정리 작업 불필요 |
| Redis가 재시작하며 쪽지를 잃음, 또는 DB 기록 뒤 등록 전에 API가 죽음 | 어디에도 없음 | 다시 배달할 쪽지가 없으므로 정리 작업이 DB를 보고 다시 등록 |

무엇을 하는지는 신뢰성 설계에 정해져 있지만, **누가 정해진 시각에 이 작업을 실행시키는지**는 정해져 있지 않았다. 워커는 발표 때 2~3개로 늘린다([신뢰성 설계](../design/reliability.md) 5.1).

## 검토한 선택지

| 선택지 | 장점 | 단점 | 결론 |
|---|---|---|---|
| 워커가 시작할 때 반복 루프를 띄움 | 컨테이너가 늘지 않음 | 워커 수만큼 정리 작업이 겹쳐 돈다. 같은 멈춘 세트를 여러 번 다시 등록하면 그 세트의 `run_count`가 그만큼 올라, 실제로는 한 번도 처리되지 않았는데 상한에 닿아 `failed`가 될 수 있다 | 탈락 |
| 호스트 cron이 정리 명령을 실행 | 익숙함 | 시연 기기마다 Compose 밖에 설정이 생긴다(운영체제마다 방법이 다름). 정리 작업에 CLI 진입점이 따로 필요하다 | 탈락 |
| **Taskiq 스케줄러 한 개** | 이미 쓰는 Taskiq 기능. 정리 작업은 보통 Taskiq 작업으로 두고 실행은 워커가 한다. Compose 안에서 끝난다 | 컨테이너가 하나 늘고, **반드시 한 개만** 띄워야 한다 | **채택** |

## 결정

Compose 서비스 `scheduler`로 `taskiq scheduler`를 하나 띄운다. 정리 작업은 워커가 실행하는 일반 Taskiq 작업이고, 스케줄러는 주기마다 그 작업의 쪽지만 넣는다. 주기는 1시간 [가설]이다.

업로드한 세트의 처리는 API가 업로드 즉시 쪽지를 넣고 워커가 바로 꺼내 처리하므로 스케줄러와 무관하다. 스케줄러가 넣는 쪽지는 정리 작업 하나뿐이고, 1시간 주기도 정리 작업에만 해당한다.

사용법 [확인]([Taskiq — Scheduling tasks](https://taskiq-python.github.io/guide/scheduling-tasks.html), 2026-11-11 확인):

- 실행: `taskiq scheduler 모듈:scheduler`. 스케줄러 객체는 `TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])`로 만든다.
- 주기 지정: 작업 정의에 라벨로 붙인다. 예: `@broker.task(schedule=[{"cron": "0 * * * *"}])`(매시 정각). 시간대가 없는 시각은 UTC로 본다. 매시 정각 주기는 시간대와 무관하다.
- 역할: "The scheduler doesn't execute tasks. It only sends them." 실행은 워커가 한다.

보내는 시점 [확인]([taskiq 소스 `taskiq/cli/scheduler/run.py`](https://github.com/taskiq-python/taskiq/blob/master/taskiq/cli/scheduler/run.py), master, 2026-11-11 확인):

- 1초마다 지금 분이 cron 식과 맞는지 보고, 맞으면 보낸다. 그 분에 이미 보냈는지는 메모리에만 기록한다.
- 꺼져 있던 동안 놓친 실행을 나중에 몰아 보내지 않는다.
- 시작할 때도 같은 판정이라, cron 식과 맞는 분에 시작했을 때만 바로 보낸다. `--skip-first-run`은 시작한 분을 이미 보낸 것으로 표시한다.

## 결과

- `scheduler`는 항상 한 개다. 발표 때 워커를 늘려도 스케줄러는 늘리지 않는다. 공식 문서도 "Please always run only one instance of the scheduler!"라고 하며, 여러 개면 작업이 그 수만큼 실행된다고 경고한다 [확인].
- 스케줄러가 멈추면 정리가 밀릴 뿐, 사용자 요청(업로드·조회·채점)과 업로드한 세트의 처리에는 영향이 없다. 다만 그동안 쪽지를 잃은 세트는 다시 등록되지 않는다. 그래서 Compose의 `scheduler`에 `restart: unless-stopped`를 두고, 정리 작업은 실행할 때마다 처리 건수를 로그 한 줄로 남긴다. 이 로그가 1시간 넘게 없으면 스케줄러가 멈춘 것이다.
- 정리 작업도 두 번 돌아도 결과가 같게 만든다(이미 지운 것은 건너뜀). 보낸 기록이 메모리에만 있으므로 정각에 재시작하면 같은 분에 한 번 더 보낼 수 있는데, 이 성질 덕분에 무해하다. 그래서 `--skip-first-run`은 쓰지 않는다.
- 재등록은 정리 작업 한 곳에서만 한다. 워커 작업([#52](https://github.com/best-vibing/cloze/issues/52))은 재등록하지 않고 `run_count`만 확인한다.
- 놓친 실행을 몰아 보내지 않아도 문제없다. 정리 작업은 실행할 때마다 DB 상태 전체를 훑으므로 다음 실행이 밀린 것을 함께 처리한다. 스케줄러가 살아 있으면 쪽지를 잃은 세트가 다시 등록되기까지 최대 약 90분(멈춤 판정 30분 + 주기 60분)이 걸린다.

## 재검토 조건

- 주기 작업이 정리 하나뿐인데 스케줄러 유지가 부담이 되면 cron 방식을 다시 본다.
- 워커를 여러 기기에 나눠 띄우게 되면, 스케줄러 한 개 규칙을 기기 단위가 아니라 서비스 전체 단위로 지켜야 한다.
