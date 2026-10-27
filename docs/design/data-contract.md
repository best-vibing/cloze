# 상세 설계서 — 데이터 계약(core 데이터 모양·API·오류 코드)

- 상위 문서: [아키텍처 정의서](../architecture.md) 3장
- 관련 결정: [ADR-006 식별자와 접근 노출](../decisions/ADR-006-ids-and-not-found.md)
- 상태: **버전 0.** 스파이크(로드맵 0~2단계)가 끝나면 한 번 고친다. 필드를 더하기는 쉽고 빼기는 어려우므로 확실히 쓰는 것만 넣었다.
- 소유: `cloze/core/models`는 팀 공동 소유. 바꿀 때는 PR 리뷰를 받는다.

## 1. core 계약

### 1.1 정책(Policy): 파이프라인의 입력

| 필드 | 값 | 기본값 |
|---|---|---|
| `preset` | `auto` / `vocab`(어휘 문맥) / `concept`(개념 용어) / `number`(수치·날짜) / `definition`(정의) | `auto` |
| `density` | 단어 100개당 빈칸 수 | 프리셋별 기본값 [가설] |
| `language` | `auto` / `en` / `ko` | `auto` |
| `include_numbers` | 숫자를 빈칸 후보에 넣을지 | `number` 프리셋만 `true` |
| `seed` | 정수. 서버가 무작위로 정한다 | — |

머리글·바닥글 같은 제외 영역은 정책이 아니라 `filter` 규칙으로 둔다.

### 1.2 단계 사이 계약

| 단계 | 입력 | 출력 | 담당 |
|---|---|---|---|
| `extract` | PDF 바이트 | 페이지마다 `{page 정보, 이미지(메모리), tokens}` | PDF 처리 |
| `filter` | **문서 전체 페이지**의 tokens, 정책 | 페이지마다 `{후보 token id 목록, 제외한 id와 사유}` | LLM·검증 |
| `analyze` | 대표 페이지 tokens, `ask_llm` | `{doc_type, preset, reason, source: llm / default}` | LLM·검증 |
| `select` | 페이지 tokens, 후보 id, 정책, `ask_llm`, 직전 실패 사유(재시도 때) | `{spans: [[token id…]], 칸별 type·hint·variants, source: gemini / rule}` | LLM·검증 |
| `gate` | 페이지 tokens, select 결과, 정책 | `{blanks, 탈락 칸과 사유, quality, passed}` | LLM·검증 |
| `redact` | 페이지 이미지, blanks의 bbox | 가린 이미지 | PDF 처리 |
| `pipeline` | 위 함수들 | 페이지별 최종 결과(1.4) | 공동 |

`filter`가 문서 전체를 받는 이유: 머리글·바닥글은 여러 페이지에서 같은 위치에 반복되는 글로 판정하므로 한 페이지만 봐서는 알 수 없다.

`cli`는 `--dump-stages`를 주면 단계마다 중간 결과를 JSON으로 남긴다. 담당자끼리 실제 파일을 주고받으며 계약을 맞춰 보기 위해서다.

### 1.3 `ask_llm` 인터페이스와 오류 구분

```
ask_llm(prompt: 문자열, schema: 응답 JSON 스키마, image: 선택) → 파싱된 dict
```

오류 종류는 `cloze/core/errors`에 정의하고, `infra/gemini`가 실제 오류를 이 종류로 바꿔 던진다(infra가 core를 import하는 방향이라 의존 규칙에 맞는다).

| 오류 | 언제 | `pipeline`의 처리 | 작업 결과 |
|---|---|---|---|
| `LLMTransientError` | 429·5xx·타임아웃이 infra 재시도(3회) 뒤에도 계속됨 | 잡지 않고 올려 보낸다 | 작업 재실행(끝난 페이지는 건너뜀) |
| `LLMInvalidResponse` | 응답을 파싱할 수 없거나 스키마와 다름 | 검증 실패로 취급: 사유를 붙여 1회 재시도 → 규칙 기반 | 계속 진행 |
| `LLMPermanentError` | 인증 실패, 설정 오류, 없는 모델 | 잡지 않고 올려 보낸다 | 작업이 잡아 `failed` + 사유로 기록하고 정상 종료(재시도하지 않음) |

### 1.4 페이지 결과(최종 출력)

`pipeline`의 출력이다. `cli`는 이 모양을 JSON 파일로 저장하고, 프론트 스파이크는 이 파일을 가짜 데이터로 쓴다.

```json
{
  "schema_version": 0,
  "page": {"page_no": 3, "width_pt": 720, "height_pt": 540, "source": "textlayer"},
  "tokens": [
    {"id": 12, "text": "Mitochondria", "bbox": [0.12, 0.31, 0.29, 0.35],
     "line_id": 4, "block_id": 1, "font_size": 18.0, "font_color": "#1a1a1a", "conf": 1.0}
  ],
  "blanks": [
    {"token_ids": [12], "answer": "Mitochondria", "bbox": [0.115, 0.305, 0.295, 0.355],
     "type": "term", "hint": "세포 소기관", "variants": [], "answer_group": null}
  ],
  "selection": {"source": "gemini", "attempts": 1, "quality": {"survival_ratio": 0.8}}
}
```

| 필드 | 뜻 |
|---|---|
| `schema_version` | 계약 버전. 필드를 바꿀 때 올린다 |
| `page.source` | `textlayer` 또는 `ocr` |
| `tokens[].id` | 페이지 안에서만 고유한 정수. LLM에는 이 id로 보내고 이 id로 받는다 |
| `bbox` | `[x0, y0, x1, y1]`, 페이지 기준 0~1, 좌상단 원점 |
| `line_id`, `block_id` | 같은 줄·같은 문단 판정용. 품질 게이트의 "주변 문맥"과 구 연속성 검사에 쓴다 |
| `conf` | 인식 신뢰도. 텍스트 레이어는 1.0 |
| `blanks[].bbox` | 묶인 토큰 bbox의 합집합 + 패딩 |
| `blanks[].type` | `text`, `number`, `term` |
| `selection.source` | `gemini`, `fallback`(규칙 기반), `none`(게이트 미달로 빈칸 없음) |

## 2. API

모든 ID는 무작위 UUID다. 작업 ID와 빈칸 세트 ID는 같다(ADR-006).

**중복 요청 방지:** 세트를 만드는 두 요청(`POST /documents`, `POST /documents/{id}/blank-sets`)은 `Idempotency-Key` 헤더를 받는다. 클라이언트가 사용자 동작 한 번마다 UUID 하나를 만들어 보내고, 같은 키로 다시 오면 서버는 새로 만들지 않고 기존 세트를 돌려준다. 다시 만들기의 seed는 서버가 무작위로 정하므로, seed로는 중복 클릭을 막을 수 없기 때문이다.

| API | 요청 | 성공 응답 |
|---|---|---|
| `POST /documents` | multipart: `file`, `preset`(기본 `auto`). 헤더 `Idempotency-Key` | `202` `{document_id, blank_set_id}`. 같은 사용자가 같은 PDF를 올리면 기존 `document_id`를 돌려준다 |
| `POST /documents/{id}/blank-sets` | `{preset}`. 헤더 `Idempotency-Key` | `202` `{blank_set_id}`. 같은 요청 키면 기존 세트 ID |
| `GET /documents` | — | `200` 내 문서 목록, 문서마다 세트 요약(`blank_set_id`, `preset_applied`, `status`, `created_at`) |
| `GET /blank-sets/{id}` | — | `200` 아래 예시. 폴링 겸용 |
| `POST /blank-sets/{id}/attempts` | `{answers: {blank_id: 입력}}` | `200` `{attempt_id, results: {blank_id: correct / near / wrong}, score}` |
| `GET /attempts/{id}/reveal` | — | `200` `{answers: {blank_id: 정답}}`. 제출한 시도에 대해서만 |
| `DELETE /documents/{id}` | — | `204`. 삭제 표시(`deleted_at`)를 하고 바로 응답한다. 이후 목록·조회에서 보이지 않고, 세트·빈칸·시도·이미지의 실제 삭제는 정리 작업이 한다 |

`GET /blank-sets/{id}` 응답 예시(완료 시). **정답은 들어 있지 않다.**

```json
{
  "blank_set_id": "6f1c…",
  "status": "done",
  "progress": {"done": 12, "total": 12},
  "preset_requested": "auto",
  "preset_applied": "concept",
  "preset_reason": "강의 슬라이드로 보여 개념 용어 방식을 골랐어요",
  "pages": [
    {"page_no": 1, "image_url": "https://…(짧게 만료되는 서명 URL)", "width_pt": 720, "height_pt": 540,
     "blanks": [{"blank_id": "9a2e…", "bbox": [0.115, 0.305, 0.295, 0.355], "type": "term", "hint": "세포 소기관"}]}
  ]
}
```

`queued`·`running`일 때는 `pages`가 비어 있고 `progress`만 의미가 있다. `failed`일 때는 `error: {code, message}`가 들어간다.

| `status` | 뜻 |
|---|---|
| `queued` | 작업 등록됨, 아직 워커가 받지 않음 |
| `running` | 처리 중 |
| `done` | 모든 페이지 처리 완료 |
| `partial` | 끝났지만 일부 페이지가 실패했거나 빈칸 없이 나감 |
| `failed` | 결과를 낼 수 없음(인식 실패, 영구 오류 등) |

`progress`는 `{done: 처리를 마친 쪽 수, total: 전체 쪽 수}`다. 실패한 쪽도 "마친" 것으로 센다. 그래야 `done = total`이 "작업이 끝났다"와 같은 뜻이 된다.

## 3. 오류 형식과 코드

모든 오류는 같은 모양이다: `{"error": {"code": "…", "message": "사용자에게 보여 줄 문장"}}`

| 코드 | HTTP | 언제 |
|---|---|---|
| `unauthorized` | 401 | 로그인하지 않음 |
| `not_found` | 404 | 없는 리소스, **또는 남의 리소스**(존재 여부를 숨긴다, ADR-006) |
| `not_ready` | 409 | 아직 처리 중인 세트에 답안 제출 |
| `file_too_large` | 413 | 파일 크기 상한 초과 |
| `not_pdf` | 415 | MIME 시그니처가 PDF가 아님 |
| `invalid_request` | 422 | 요청 값이 계약과 다름(없는 프리셋 값, 헤더 누락 등) |
| `encrypted_pdf` | 422 | 암호화되었거나 JavaScript가 포함된 PDF |
| `too_many_pages` | 422 | 쪽수 상한 초과 |
| `daily_limit_exceeded` | 429 | 사용자 하루 처리 쪽수 초과 |
| `too_many_running` | 429 | 사용자의 진행 중 세트 수 초과 |
| `attempt_limit_exceeded` | 429 | 세트당 제출 횟수 초과 |
| `service_limit_exceeded` | 503 | 서비스 전체의 하루 Gemini 호출 상한 초과. 작업을 등록하지 않고 거절한다 |

한도의 초기값은 [신뢰성 설계](reliability.md) 5절에 둔다.

## 4. 데이터 모델

```
users 1─N documents 1─N blank_sets 1─N blanks
              │              ├─1─N blank_set_pages
              └─1─N pages    └─1─N attempts N─1 users
```

| 테이블 | 주요 필드 | 메모 |
|---|---|---|
| `users` | id, 외부 로그인 식별자, email, created_at | 인증 방식(아키텍처 2.1)이 정해지기 전이라 최소 필드만 |
| `documents` | id, owner_id, sha256, 파일명, page_count, 원본 키, created_at, expires_at, deleted_at | `(owner_id, sha256)` 고유. 처리 상태 필드 없음(업로드 검사는 요청 안에서 끝난다). `deleted_at`은 삭제 표시 |
| `pages` | (document_id, page_no), 크기(pt), source, 원본 이미지 키, 토큰 JSON 키 | 문서당 한 번만 만들고 세트끼리 재사용 |
| `blank_sets` | id(= 작업 ID), document_id, requested_by, idempotency_key, preset_requested, preset_applied, analysis, seed, model, prompt_version, status, progress, error_code, error_message, created_at, finished_at | 상태의 유일한 기준. `(requested_by, idempotency_key)` 고유. `prompt_version`은 기록용 |
| `blank_set_pages` | (set_id, page_no), 가린 이미지 키, selection_source, quality | 가린 이미지는 세트마다 다르다. 이 행이 있으면 그 페이지는 처리가 끝난 것(재실행 시 건너뜀) |
| `blanks` | id(= `blank_id`), set_id, page_no, 순서, bbox, type, hint, **answer(서버 전용)**, variants, answer_group | — |
| `attempts` | id, set_id, user_id, submitted_at, answers, results, score | — |
