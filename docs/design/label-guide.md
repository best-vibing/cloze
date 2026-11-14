# 라벨 기준 — 품질 게이트 기준값 보정 1차 (#40)

> **초안.** 아래 좋음 / 애매 / 나쁨 기준과 예시는 팀이 확정하기 전의 제안이다. 확정하면 이 표시를 지운다.

[품질 게이트 2.5절](selection-and-quality-gate.md#25-기준값-보정-로드맵-2단계-완료-조건)의 2단계(사람이 칸마다 라벨 붙이기)를 위한 기준이다. 1차는 규칙 기반 선택 결과만 라벨한다. Gemini 결과(2차)는 #43 이후에 같은 PDF·같은 라벨러·같은 기준으로 한다.

## 1. 시트 만들기

1. PR #73(한국어 어절 나누기, `EXTRACT_VERSION` 3)이 `develop`에 머지된 뒤에 뽑는다. 그 전 결과는 한국어 어절이 붙어 있어 라벨을 다시 해야 한다.
2. 수업 PDF 5~10개를 레포 밖 폴더에 두고 `.env`에 `CLOZE_SAMPLES_DIR=<폴더>`를 적는다.
3. 시트를 만든다. seed 0, density 5로 고정이라 누가 뽑아도 같은 시트가 나온다.

   ```
   uv run python -X utf8 -m cloze.cli.label_sheet make --out out/labels/rule.csv
   ```

4. 라벨러마다 파일을 복사한다(예: `out/labels/rule_<GitHub-id>.csv`). **서로의 라벨을 보지 않고** 따로 붙인다.
5. 칸이 실제로 어떻게 보이는지는 같은 설정의 이미지로 본다. 시트의 `order`가 `page_NNN.json`의 `order`와 같다.

   ```
   uv run python -X utf8 -m cloze.cli mask <pdf> --out out/mask/<이름> --density 5 --seed 0
   ```

6. 다 붙였으면 검사한다. label이 세 값 중 하나인지, labeler가 있는지 본다.

   ```
   uv run python -X utf8 -m cloze.cli.label_sheet check out/labels/rule_<GitHub-id>.csv
   ```

수업 PDF와 시트(`out/` 아래)는 저작권 때문에 커밋하지 않는다. `.gitignore`의 `*.pdf`, `out/`에 걸린다. 엑셀에서 저장할 때는 형식을 **CSV UTF-8**로 고른다. 그냥 CSV로 저장하면 한글이 깨진다.

## 2. 열

| 열 | 뜻 |
|---|---|
| `file`, `page`, `order` | 칸의 위치. 셋을 합친 값이 칸 하나를 가리킨다 |
| `answer` | 정답 글자(앞뒤 문장 부호를 뗀 것) |
| `context_before`, `context_after` | 정답 앞뒤 토큰 6개. 다른 빈칸은 `[글자]`로 표시 |
| `extract_version` | 뽑을 때의 추출 버전. 다르면 같은 칸이 아닐 수 있다 |
| `answer_len`, `hangul` | 길이 게이트가 보는 값: 조사를 뗀 글자 수, 한글이 있으면 1 |
| `exposure_count` | 같은 쪽의 가려지지 않은 곳에 같은 단어가 나온 횟수(정답 노출) |
| `page_target`, `page_survival` | 그 쪽의 목표 칸 수, 고른 칸 수 ÷ 목표 칸 수 |
| `score`, `*_signal` | 규칙 선택기의 점수와 신호(크기·굵기·제목·빈도) |
| `label` | `좋음` / `애매` / `나쁨` 중 하나. 다른 값은 쓰지 않는다 |
| `labeler` | 라벨을 붙인 사람의 GitHub id |

보정 계산(PR #76 `parse_label_csv`)에는 `key_columns=("file", "page", "order")`, `label_column="label"`, `value_column=<지표 열>`을 넘긴다. 계산은 라벨러 한 명의 파일씩 따로 한다. 한 파일에 같은 칸이 두 번 나오면 오류가 난다.

## 3. 기준

질문 하나로 판단한다: **"이 칸을 가린 채로 학생이 복습하면, 맞히려고 떠올려야 하는 것이 이 수업의 내용인가?"**

| 라벨 | 뜻 |
|---|---|
| 좋음 | 수업의 핵심 용어·개념이고, 앞뒤 문맥을 보면 무엇을 떠올려야 하는지 알 수 있다 |
| 애매 | 내용과 관련은 있지만 핵심은 아니거나, 답이 여러 개일 수 있다 |
| 나쁨 | 내용을 몰라도 맞히거나(정답 노출, 너무 흔한 말), 떠올릴 근거가 없거나, 칸 자체가 잘못됐다 |

### 좋음

- 정의 문장 "**Momentum** keeps a running average of past gradients"의 `Momentum`. 정의를 보고 용어를 떠올린다.
- "HTTP 메서드 중 자원을 새로 만드는 것은 **POST**"의 `POST`. 수업에서 배운 짝(메서드-용도)을 떠올려야 한다.
- 한국어 본문 "표본평균의 분포는 **정규분포**에 가까워진다"의 `정규분포`. 개념의 결론이고 문맥이 답을 하나로 좁힌다.

### 애매

- "the loss shrinks until **convergence** appears"의 `convergence`. 관련 용어지만 이 문장만으로는 "zero", "the end" 같은 다른 답도 자연스럽다.
- 제목 줄의 `Backend`처럼 슬라이드 주제어가 제목에만 있고 본문에서 설명하지 않을 때. 떠올릴 근거가 같은 쪽에 없다.
- 두 칸이 바로 붙어 있어(`[Mean] [Squared] Error`) 하나만 보면 좋지만 함께 가려지면 풀기 어려울 때.

### 나쁨

- `exposure_count`가 1 이상이고, 바로 다음 줄에 같은 단어가 그대로 보여 베껴 쓸 수 있을 때.
- 머리글·바닥글·강사 이름·과목 번호가 칸이 된 경우(예: 바닥글 "Cloze Sample Handbook"의 `Handbook`). 내용이 아니다.
- 줄바꿈이나 추출 오류로 단어가 잘리거나 붙은 경우(`학습률이작으면`, `optimi-`). 칸 자체가 틀렸다.

### 판단이 갈릴 때

- 정답이 맞는지가 아니라 **칸으로서 좋은지**를 본다. 정답 글자가 틀렸으면(추출 오류) 나쁨이다.
- 다른 칸 때문에 나빠진 경우(붙어 있는 칸, 같은 줄에 칸이 몰림)는 애매로 두고, 어느 칸이 문제인지 팀에서 따로 모은다.
- 망설여지면 애매. 애매는 보정 계산에서 좋음·나쁨의 분모에 들어가지 않는다.
