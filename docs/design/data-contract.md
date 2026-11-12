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
| `select` | 페이지 tokens, 후보 id, 정책, `ask_llm`, 직전 실패 사유(재시도 때), 피할 token id(같은 문서의 이전 세트에서 쓴 것) | `{spans: [[token id…]], 칸별 type·hint·variants, source: gemini / rule}` | LLM·검증 |
| `gate` | 페이지 tokens, select 결과, 정책 | `{blanks, 탈락 칸과 사유, quality, passed}`. blanks마다 `order`(페이지 안 읽는 순서: 줄 → x좌표), `answer_group`(**같은 페이지 안**에서 같은 정답인 칸끼리), 토큰에서 가져온 글자 크기·색을 채운다 | LLM·검증 |
| `redact` | 페이지 이미지, blanks의 bbox | 가린 이미지, 칸마다 칠한 색(`style.bg_color`로 들어감). 빈칸이 없어도 가린 이미지를 만든다 | PDF 처리 |
| `pipeline.prepare` | PDF 바이트 | 문서 추출 결과(페이지별 page 정보·이미지·tokens). 워커는 이것을 L2로 저장한다 | 공동 |
| `pipeline.build` | 문서 추출 결과(새로 만든 것 또는 저장된 것), 정책, `ask_llm`, 건너뛸 페이지 번호, 저장된 분석 결과(있으면), 피할 token id | 페이지별 결과(1.4)를 **한 페이지씩 차례로 내보낸다.** 워커는 받는 대로 저장한다(L4) | 공동 |

규칙 기반 선택기(`select/rule`)는 `ask_llm` 대신 문서 통계(`doc_stats`: 정규화한 단어의 빈도, 제목급 줄의 단어)를 받는다.

`pipeline`을 `prepare`와 `build` 둘로 나눈 이유: 추출 결과를 저장했다가 다시 만들기에서 재사용하고(L2), 재실행 때 끝난 페이지를 건너뛰려면(L4) 워커가 중간에 끼어들 수 있어야 하기 때문이다.

`filter`가 문서 전체를 받는 이유: 머리글·바닥글은 여러 페이지에서 같은 위치에 반복되는 글로 판정하므로 한 페이지만 봐서는 알 수 없다. 기준을 넘는 위치 묶음은 모두 뺀다(두 장 모아찍기 PDF는 바닥글이 한 쪽에 두 번 나온다). 그렇게 확인된 바닥글 문구로 문서의 다른 곳(표지 등)에서 **줄 전체가 바닥글 구절로만 이루어져 있으면** 그 줄의 토큰도 `footer_phrase`로 뺀다. 본문 문장 속에 같은 구절이 나오면("Vibe coding is a way …") 학습 내용이므로 후보로 남긴다. 구절은 바닥글 줄을 구분 기호나 큰 간격으로 나눈 것이고, 토큰 두 개 이상인 구절만 맞춘다. 한 단어로 맞추면 본문의 정상 단어까지 빠지기 때문이다. 비교는 `word_key`(대소문자·앞뒤 부호·조사 무시)로 한다. 값과 규칙은 [`core/filter`](../../cloze/core/filter.py)가 기준이다.

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
| `LLMPermanentError` | 인증 실패, 설정 오류, 없는 모델 | 그 세트의 남은 페이지는 LLM을 부르지 않고 규칙 기반으로 만든다. 분석이면 기본 방식 | 계속 진행. 운영 경보를 즉시 보낸다 |

### 1.4 페이지 결과(최종 출력)

`pipeline`의 출력이다. `cli`는 이 모양을 JSON 파일로 저장하고, 프론트 스파이크는 이 파일을 가짜 데이터로 쓴다.

```json
{
  "schema_version": 2,
  "page": {"page_no": 3, "width_pt": 720, "height_pt": 540, "source": "textlayer"},
  "tokens": [
    {"id": 12, "text": "Mitochondria,", "bbox": [0.12, 0.31, 0.30, 0.35], "word_bbox": [0.12, 0.31, 0.29, 0.35],
     "line_id": 4, "block_id": 1, "font_size": 18.0, "fontname": "Helvetica-Bold",
     "font_color": "#1a1a1a", "conf": 1.0}
  ],
  "blanks": [
    {"token_ids": [12], "answer": "Mitochondria", "bbox": [0.115, 0.305, 0.295, 0.355],
     "type": "term", "hint": "세포 소기관", "variants": [], "answer_group": null,
     "style": {"font_size_ratio": 0.025, "font_color": "#1a1a1a", "bg_color": "#ffffff"}}
  ],
  "selection": {"source": "gemini", "attempts": 1, "quality": {"survival_ratio": 0.8}}
}
```

| 필드 | 뜻 |
|---|---|
| `schema_version` | 계약 버전. 필드를 바꿀 때 올린다. 2: `tokens[].word_bbox` 추가 |
| `page.source` | `textlayer` 또는 `ocr` |
| `tokens[].id` | 페이지 안에서만 고유한 정수. LLM에는 이 id로 보내고 이 id로 받는다 |
| `bbox` | `[x0, y0, x1, y1]`, 페이지 기준 0~1, 좌상단 원점 |
| `tokens[].word_bbox` | 앞뒤 문장 부호를 뺀 글자들의 bbox. 뗄 부호가 없으면 `null`(= `bbox`). 정답과 칠하는 범위는 이것을 쓴다. 이 필드가 없는 옛 토큰(`extract_version` 1)은 추출 버전이 달라 L2에서 재사용되지 않는다 |
| `line_id`, `block_id` | 같은 줄·같은 문단 판정용. 품질 게이트의 "주변 문맥"과 구 연속성 검사에 쓴다 |
| `fontname` | 폰트 이름. 규칙 기반 선택기가 굵은 글씨를 판정할 때 쓴다 |
| `conf` | 인식 신뢰도. 텍스트 레이어는 1.0 |
| `blanks[].answer` | 토큰 원문에서 앞뒤 문장 부호를 뗀 글자. 한국어 조사는 떼지 않는다(v1은 어절 단위, [채점 1.1](grading-and-overlay.md#11-판정-순서)) |
| `blanks[].bbox` | 묶인 토큰 `word_bbox`(없으면 `bbox`)의 합집합 + 패딩. 문장 부호는 칠하지 않는다. 부호를 뗀 변에는 패딩을 붙이지 않는다(패딩이 글자와 부호 사이 여백보다 커서 부호를 덮기 때문) |
| `blanks[].type` | `text`, `number`, `term` |
| `blanks[].style` | 입력칸 표시 정보(글자 크기 비율·글자색·배경색). [채점·입력칸 표시](grading-and-overlay.md) 3.1 |
| `selection.source` | `gemini`, `fallback`(규칙 기반), `none`(게이트 미달로 빈칸 없음) |

## 2. API

모든 ID는 무작위 UUID다. 작업 ID와 빈칸 세트 ID는 같다(ADR-006).

**중복 요청 방지:** 세트를 만드는 두 요청(`POST /documents`, `POST /documents/{id}/blank-sets`)은 `Idempotency-Key` 헤더를 받는다. 클라이언트가 사용자 동작 한 번마다 UUID 하나를 만들어 보내고, 같은 키로 다시 오면 서버는 새로 만들지 않고 기존 세트를 돌려준다. 다시 만들기의 seed는 서버가 무작위로 정하므로, seed로는 중복 클릭을 막을 수 없기 때문이다. 같은 키에 **다른 내용**(다른 파일, 다른 방식)이 오면 `invalid_request`로 거절한다. 이를 위해 요청 내용의 지문(파일 sha256과 방식)을 세트에 함께 저장한다.

| API | 요청 | 성공 응답 |
|---|---|---|
| `POST /auth/login` | `{login_id, password}` | `204`. 서명된 세션 쿠키(HttpOnly, Secure, SameSite=Lax, 7일 [가설]) 발급. 틀리면 `invalid_credentials` |
| `POST /auth/logout` | — | `204`. 쿠키 삭제 |
| `GET /images/{key}` | 쿼리 `exp`(만료 시각), `sig`(서명) | `200` 이미지. 서명이 맞고 만료 전이며, 요청자가 소유자이고 문서가 삭제 표시되지 않았을 때만. 아니면 `not_found` |
| `POST /documents` | multipart: `file`, `preset`(기본 `auto`). 헤더 `Idempotency-Key` | `202` `{document_id, blank_set_id}`. 같은 사용자가 같은 PDF를 올리면 기존 `document_id`를 돌려준다 |
| `POST /documents/{id}/blank-sets` | `{preset}`. 헤더 `Idempotency-Key` | `202` `{blank_set_id}`. 같은 요청 키면 기존 세트 ID |
| `GET /documents` | — | `200` 내 문서 목록, 문서마다 세트 요약(`blank_set_id`, `preset_applied`, `status`, `created_at`) |
| `GET /blank-sets/{id}` | — | `200` 아래 예시. 폴링 겸용 |
| `POST /blank-sets/{id}/attempts` | `{answers: {blank_id: 입력}}` | `200` `{attempt_id, results: {blank_id: correct / near / wrong}, score, after_reveal}`. 횟수 제한 없음 |
| `GET /blank-sets/{id}/attempts` | — | `200` 내 시도 목록 `[{attempt_id, submitted_at, score, after_reveal, revealed_at}]`. 처음 풀이와 복습을 나눠 볼 때 쓴다 |
| `POST /attempts/{id}/reveal` | — | `200` `{answers: {blank_id: 정답}}`. 제출한 시도라면 언제든. 공개 시각을 그 시도에 기록하므로 `GET`이 아니다(미리 불러오기나 새로고침으로 공개 기록이 생기지 않게) |
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
     "blanks": [{"blank_id": "9a2e…", "order": 1, "bbox": [0.115, 0.305, 0.295, 0.355], "type": "term", "hint": "세포 소기관",
                 "style": {"font_size_ratio": 0.025, "font_color": "#1a1a1a", "bg_color": "#ffffff"}}]}
  ]
}
```

**인증:** 웹 페이지와 `POST /auth/login` 외의 모든 API와 `/images`는 세션 쿠키가 필요하다(없으면 `unauthorized`). 서버는 요청마다 사용자가 존재하고 쿠키의 `session_version`이 맞는지 확인한다. 상태를 바꾸는 요청은 `Origin`이 자기 주소일 때만 받는다(CSRF 방어). 쿠키의 Secure 속성은 설정 `COOKIE_SECURE`(기본 켬)로 로컬 개발에서만 끌 수 있다.

**계정 관리:** API가 아니라 CLI로 한다. 가입 엔드포인트는 없다(ADR-007). `create-user`(아이디·비밀번호·표시 이름), `set-password`(비밀번호 재설정, `session_version`을 올려 기존 세션을 끊음), `delete-user`(그 사용자의 문서에 삭제 표시 → 정리 작업이 실제 삭제, 세션도 바로 끊김).

`image_url`은 API의 `/images/{key}?exp=…&sig=…` 경로이고, 짧게 만료된다(만료 시간은 [신뢰성 설계](reliability.md) 2절). 이미지를 불러오다 만료로 실패하면 프론트는 `GET /blank-sets/{id}`를 한 번 다시 불러 새 URL을 받는다.

`queued`·`running`일 때는 `pages`가 비어 있고 `progress`만 의미가 있다. `failed`일 때는 `error: {code, message}`가 들어간다.

| `status` | 뜻 |
|---|---|
| `queued` | 작업 등록됨, 아직 워커가 받지 않음 |
| `running` | 처리 중 |
| `done` | 모든 페이지 처리 완료 |
| `partial` | 끝났지만 일부 페이지가 실패했거나 빈칸 없이 나감 |
| `failed` | 결과를 낼 수 없음(글자를 읽지 못함, 실행 횟수 상한 초과, 처리 중 삭제) |

`failed`일 때 `error.code`는 다음 중 하나다. HTTP 오류 코드(3절)와 별개로, 세트가 왜 끝났는지를 나타낸다.

| `error.code` | 뜻 |
|---|---|
| `unreadable_pdf` | 텍스트도 OCR도 거의 읽지 못함. "인식이 잘 안 됐어요, 다시 올려 주세요" |
| `processing_failed` | 실행 횟수 상한까지 일시적 오류·제한 시간 초과·워커 중단이 반복됨 |
| `document_deleted` | 처리 중에 문서가 삭제됨 |

`progress`는 `{done: 처리를 마친 쪽 수, total: 전체 쪽 수}`다. 실패한 쪽도 "마친" 것으로 센다. 그래야 `done = total`이 "작업이 끝났다"와 같은 뜻이 된다.

## 3. 오류 형식과 코드

모든 오류는 같은 모양이다: `{"error": {"code": "…", "message": "사용자에게 보여 줄 문장"}}`

| 코드 | HTTP | 언제 |
|---|---|---|
| `unauthorized` | 401 | 로그인하지 않음 |
| `invalid_credentials` | 401 | 아이디 또는 비밀번호가 틀림(어느 쪽이 틀렸는지 알려 주지 않는다) |
| `not_found` | 404 | 없는 리소스, **또는 남의 리소스**(존재 여부를 숨긴다, ADR-006) |
| `not_ready` | 409 | 아직 처리 중인 세트에 답안 제출 |
| `file_too_large` | 413 | 파일 크기 상한 초과 |
| `not_pdf` | 415 | MIME 시그니처가 PDF가 아님 |
| `invalid_request` | 422 | 요청 값이 계약과 다름(없는 프리셋 값, 헤더 누락 등) |
| `encrypted_pdf` | 422 | 암호화되었거나 JavaScript가 포함된 PDF |
| `too_many_pages` | 422 | 쪽수 상한 초과 |
| `daily_limit_exceeded` | 429 | 사용자 하루 처리 쪽수 초과 |
| `too_many_running` | 429 | 사용자의 진행 중 세트 수 초과 |
| `too_many_login_attempts` | 429 | 로그인 실패가 짧은 시간에 반복됨 |
| `service_limit_exceeded` | 503 | 서비스 전체의 하루 Gemini 호출 상한 초과. 작업을 등록하지 않고 거절한다 |

한도의 초기값은 [신뢰성 설계](reliability.md) 5절에 둔다.

## 4. 데이터 모델

```
users 1─N documents 1─N blank_sets 1─N blanks
              │              ├─1─N blank_set_pages
              │              ├─1─N llm_calls
              └─1─N pages    └─1─N attempts N─1 users
```

| 테이블 | 주요 필드 | 메모 |
|---|---|---|
| `users` | id, login_id(고유), password_hash, display_name, session_version, created_at | 계정은 팀이 CLI로 만든다. 비밀번호는 솔트를 붙인 느린 해시(scrypt 또는 Argon2)로만 저장한다. Google 로그인이 붙으면 외부 식별자 필드를 더한다 |
| `documents` | id, owner_id, sha256, 파일명, page_count, 원본 키, created_at, expires_at, deleted_at | `(owner_id, sha256)` 고유는 **삭제 표시가 없는 행에만** 적용한다(부분 고유 인덱스 `WHERE deleted_at IS NULL`). 삭제한 뒤 같은 PDF를 올리면 새 문서가 된다. 처리 상태 필드 없음(업로드 검사는 요청 안에서 끝난다) |
| `pages` | (document_id, page_no, extract_version), 크기(pt), source, 원본 이미지 키, 토큰 JSON 키 | 같은 추출 버전이면 세트끼리 재사용 |
| `blank_sets` | id(= 작업 ID), document_id, requested_by, idempotency_key, request_hash, preset_requested, preset_applied, analysis, seed, model, prompt_version, extract_version, status, progress, run_count, error_code, error_message, created_at, updated_at, finished_at | 상태의 유일한 기준. `(requested_by, idempotency_key)` 고유. `prompt_version`·`extract_version`은 기록용. `run_count`는 실행 횟수, `updated_at`은 진행할 때마다 갱신 |
| `blank_set_pages` | (set_id, page_no), 가린 이미지 키, selection_source, quality | 가린 이미지는 세트마다 다르다. 이 행이 있으면 그 페이지는 처리가 끝난 것(재실행 시 건너뜀) |
| `blanks` | id(= `blank_id`), set_id, page_no, 순서, token_ids, bbox, type, hint, style, **answer(서버 전용)**, variants, answer_group | `token_ids`는 다시 만들기에서 "이미 쓴 단어"를 알려 주는 데 쓴다 |
| `attempts` | id, set_id, user_id, submitted_at, answers, results, score, after_reveal, revealed_at | `after_reveal`: 같은 세트에서 정답을 본 뒤의 시도인지. 처음 풀이와 복습을 나눠 꺼내기 위한 필드 |
| `llm_calls` | id, set_id, page_no(분석이면 없음), kind(`analyze`/`select`), 시도 번호, model, 결과(`ok`/`invalid`/`transient`/`permanent`), latency_ms, 입력·출력 토큰 수, created_at | 서비스 전체 비용 상한 집계, 운영 지표(지연·비용·실패율)의 원자료 |
