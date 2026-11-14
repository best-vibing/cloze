# PDF 빈칸 학습 서비스

수업 PDF를 올리면 중요한 단어를 가려 빈칸 문제로 만들어 주는 서비스.

브랜치·커밋·PR·완료 기준 같은 팀 작업 방식은 [작업 규칙](CONTRIBUTING.md)에 있다.

**MOST IMPORTANT**
프롬프트를 강의자료에 따라 AI로 만든 후 발표자료에 충실히 만들었음을 넣어 강조한다.
확대는 막는다. 누군가 발표시에 질문하면, "그렇다면 당신은 시험지를 확대해서 풀 것이냐"라고 한다.


## 설계 문서

| 문서 | 담는 것 | 담당 |
|---|---|---|
| [아키텍처 정의서](docs/architecture.md) | 부품과 연결, 확정된 결정의 요약, 처리 흐름 | 공동 |
| [아키텍처 결정 기록(ADR)](docs/decisions/) | 결정마다 배경·검토한 선택지·결과 | 결정한 사람 |
| [상세 설계 — 데이터 계약](docs/design/data-contract.md) | core 데이터 모양, API 요청·응답, 오류 코드, 테이블 필드 | 공동 |
| [상세 설계 — PDF 처리·좌표 추출](docs/design/pdf-extraction.md) | 렌더링, 좌표 규약, 폰트 필드, OCR 경로 | PDF 처리 |
| [상세 설계 — 단어 선택·자료 분석·품질 게이트](docs/design/selection-and-quality-gate.md) | 자동 모드 분석, 품질 게이트 기준과 보정 절차, LLM 출력 검증, 프롬프트 입력 형식, 운영 지표 | LLM·검증 |
| [상세 설계 — 채점·입력칸 표시](docs/design/grading-and-overlay.md) | 채점 판정 순서, 정답 공개·다시 풀기 기록, 입력칸 글자 크기·색과 입력 규칙 | 서버·워커, 프론트 |
| [상세 설계 — 신뢰성](docs/design/reliability.md) | 실패 담당, 재시도 층, 시간 설정, 멱등성, 한도 | 서버·워커 |
| [이슈 해설서](docs/이슈-해설서.md) | 이슈(#12~#61)마다 왜 필요한지·지금 상태·할 일 풀이·확인 방법, 구현 후 부품 사이 데이터 흐름 그림 | 공동 |

## 문서 규칙

1. **한 사실은 한 곳에만 쓴다.** 숫자·절차·필드 목록은 한 문서에만 두고, 다른 문서는 요약 한 줄과 링크만 둔다.
2. **어디에 쓰나:** 한 모듈 안의 동작은 그 모듈 문서에, 여러 모듈에 걸친 규칙은 주제 문서(데이터 계약, 신뢰성)에 쓴다. 결정의 "왜"는 ADR에 쓴다.
3. **새 파일은 새 담당 영역이 생겼을 때만** 만든다. 그 밖에는 기존 문서에 절을 추가한다.
4. **구현되면 코드가 기준이다.** 데이터 모양은 pydantic 모델과 FastAPI가 만드는 OpenAPI 문서가, 한도 값은 설정 파일이 기준이 된다. 그때 문서의 해당 표는 지우고 코드로 링크한다. 상세 설계서는 구현이 진행될수록 줄어드는 것이 정상이다.
5. **ADR은 고치지 않는다.** 결정이 바뀌면 새 ADR을 쓰고, 옛 ADR의 상태를 "대체됨(ADR-번호)"으로 바꾼다. 새 ADR에는 "관련 드라이버" 절을 두고 [아키텍처 정의서 0장](docs/architecture.md#0-아키텍처-드라이버와-품질-목표)의 ID(D1~D8)를 적는다.
6. 문서를 추가하거나 지우면 위 표도 함께 고친다.

## 개발 환경

Python 버전과 라이브러리 버전은 팀 전체가 같게 쓴다. `uv`가 `.python-version`(3.13)과 `uv.lock`을 보고 똑같이 맞춰 준다.

1. [uv](https://docs.astral.sh/uv/) 설치
2. `uv sync` — Python 3.13이 없으면 uv가 받아 오고, 잠금 파일 그대로 의존성을 설치한다
3. `.env.example`을 복사해 `.env`를 만들고 값을 채운다(`.env`는 커밋하지 않는다)

| 명령 | 하는 일 | CI에서도 실행 |
|---|---|---|
| `uv run ruff check .` | 린트(순환 복잡도 10 초과도 실패) | 예 |
| `uv run ruff format .` | 자동 포맷(CI는 `--check`로 확인만) | 예 |
| `uv run mypy cloze tests` | 타입 검사(strict) | 예 |
| `uv run bandit -r cloze -q` | 보안 취약점 검사 | 예 |
| `uv run pytest` | 테스트 | 예 |

PR을 올리면 CI(`.github/workflows/ci.yml`)가 위 다섯 가지를 실행한다. 모두 통과해야 머지한다. 의존성을 추가할 때는 `uv add <패키지>`로 넣어 `uv.lock`도 함께 커밋한다.

### DB (PostgreSQL)

[Docker](https://docs.docker.com/get-started/get-docker/)로 내 컴퓨터에 띄운다. 주소는 `config.py` 기본값과 같아서 `.env`에 따로 적지 않아도 된다.

```
docker compose up -d postgres     # 띄우기 (데이터는 볼륨 pgdata에 남는다)
uv run alembic upgrade head       # 테이블을 최신 마이그레이션까지 맞추기
docker compose down               # 끄기 (-v를 붙이면 데이터도 지운다)
```

- 5432 포트를 이미 다른 PostgreSQL이 쓰고 있으면 `docker-compose.yml`의 왼쪽 포트를 바꾸고, `.env`의 `CLOZE_DATABASE_URL` 주석을 풀어 같은 포트로 고친다.
- DB가 꺼져 있으면 DB 테스트는 건너뛴다. CI는 PostgreSQL을 함께 띄우므로 건너뛰지 않고, 연결에 실패하면 테스트가 실패한다.
- 테이블을 추가하거나 바꿀 때는 `uv run alembic revision --autogenerate -m "설명"`으로 파일을 만들고, 생성된 내용을 꼭 읽어 본다(이름 바꾸기를 지우고 새로 만들기로 잘못 읽는 경우가 있다). 마이그레이션은 **추가만** 한다(새 테이블·빈 값 허용 컬럼·인덱스).
- 다른 사람과 동시에 마이그레이션을 만들면 기록이 두 갈래가 되어 CI의 `test_migrations_have_single_head`가 실패한다. 늦게 머지하는 쪽이 `develop`을 합친 뒤 `uv run alembic merge heads -m "merge"`로 합친다.

### 큐 (Redis·워커)

```
docker compose up -d redis              # 띄우기 (AOF가 켜져 있어 재시작해도 쌓인 작업이 남는다)
uv run taskiq worker cloze.worker:broker --max-async-tasks 1  # 워커
```

- `--max-async-tasks 1`: 워커 프로세스 하나가 작업을 하나씩만 처리한다([신뢰성 설계 5절](docs/design/reliability.md#5-한도)). taskiq 기본값은 100이라, 빠뜨리면 한 프로세스가 PDF 여러 개를 동시에 메모리에 올리고 Gemini 동시 호출 상한도 나눠 쓴다.

- 작업 제한 시간(`CLOZE_TASK_TIMEOUT_S`)은 `CLOZE_QUEUE_IDLE_TIMEOUT_MS`보다 짧아야 한다. 어기면 설정을 읽을 때 실패한다([신뢰성 설계 2절](docs/design/reliability.md#2-시간-설정-초기값)).
- 처리 중에 죽은 워커의 작업은 `idle_timeout`이 지난 **뒤 새 작업이 하나 들어올 때** 다른 워커가 넘겨받는다. 새 작업이 없으면 넘겨받지 않는다(PR의 재배달 실험).
- 재배달 실험: idle을 줄여 워커를 띄우고(`CLOZE_TASK_TIMEOUT_S=30 CLOZE_QUEUE_IDLE_TIMEOUT_MS=60000 uv run taskiq worker cloze.worker:broker scripts.redelivery_experiment --max-async-tasks 1`), `uv run python -m scripts.redelivery_experiment B`로 등록한 뒤 "받음" 로그가 나오면 워커를 강제 종료한다.

수업 PDF 같은 기준 자료는 저작권 때문에 레포에 넣지 않는다. 로컬 폴더에 두고 `CLOZE_SAMPLES_DIR`로 가리킨다. 테스트용으로 직접 만든 작은 PDF만 `tests/fixtures/`에 커밋한다.
