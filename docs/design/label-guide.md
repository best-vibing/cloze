# 라벨 기준 — 품질 게이트 기준값 보정 1차 (#40)

> **초안.** 아래 좋음 / 애매 / 나쁨 기준과 예시는 팀이 확정하기 전의 제안이다. 확정하면 이 표시를 지운다.

[품질 게이트 2.5절](selection-and-quality-gate.md#25-기준값-보정-로드맵-2단계-완료-조건)의 2단계(사람이 칸마다 라벨 붙이기)를 위한 기준이다. 1차는 규칙 기반 선택 결과만 라벨한다. Gemini 결과(2차)는 #43 이후에 같은 PDF·같은 라벨러·같은 기준으로 한다.

## 1. 시트 만들기

1. 같은 추출 버전으로 시트를 뽑는다. PR #73 이전 결과는 한국어 어절이 붙어 있어 라벨을 다시 해야 한다.
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

6. 입력 중이거나 다 붙였으면 검사한다. 숫자 범위, 추출 버전 혼용, 같은 평가자의 중복 칸도 검사한다.
   중복 여부를 비교할 때 파일명 앞뒤의 공백은 무시하고, 이름 가운데의 공백은 보존한다.

   ```
   uv run python -X utf8 -m cloze.cli.label_sheet check out/labels/rule_<GitHub-id>.csv
   ```

수업 PDF와 시트(`out/` 아래)는 저작권 때문에 커밋하지 않는다. `.gitignore`의 `*.pdf`, `out/`에 걸린다. 엑셀에서 저장할 때는 형식을 **CSV UTF-8**로 고른다. 그냥 CSV로 저장하면 한글이 깨진다.

PDF를 열 수 없으면 파일명을 표시하고 시트 쓰기를 중단한다. 텍스트를 추출하지 못했거나
빈칸이 없는 PDF는 경고하고 나머지 PDF를 처리한다. 후보 비교에 넘길 시트는 라벨을 모두 채운다.

## 2. 열

| 열 | 뜻 |
|---|---|
| `file`, `page`, `order` | 칸의 위치. 셋을 합친 값이 칸 하나를 가리킨다 |
| `answer` | 정답 글자(앞뒤 문장 부호를 뗀 것) |
| `context_before`, `context_after` | 정답 앞뒤 토큰 6개. 다른 빈칸은 `[글자]`로 표시 |
| `extract_version` | 뽑을 때의 추출 버전. 다르면 같은 칸이 아닐 수 있다 |
| `answer_len`, `hangul` | 정답 토큰의 조사를 뗀 글자 수 합계(공백 제외), 한글이 있으면 1 |
| `exposure_count` | 게이트 적용 전, 같은 쪽의 정답 등장 횟수에서 같은 정답 키로 선택한 칸 수를 뺀 값. 다른 칸이 구의 일부를 가려도 이 지표에서는 노출로 센다. 정답 키와 등장 위치는 [게이트 코드](../../cloze/core/gate.py)를 따른다. |
| `page_target`, `page_survival` | 그 쪽의 목표 칸 수, 고른 칸 수 ÷ 목표 칸 수 |
| `score`, `*_signal` | 빈칸 첫 토큰의 규칙 선택 점수와 신호(크기·굵기·제목·빈도) |
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

- `exposure_count`가 1 이상이고, 가까운 곳에 정답 전체가 그대로 보여 베껴 쓸 수 있을 때.
- 머리글·바닥글·강사 이름·과목 번호가 칸이 된 경우(예: 바닥글 "Cloze Sample Handbook"의 `Handbook`). 내용이 아니다.
- 줄바꿈이나 추출 오류로 단어가 잘리거나 붙은 경우(`학습률이작으면`, `optimi-`). 칸 자체가 틀렸다.

### 판단이 갈릴 때

- 정답이 맞는지가 아니라 **칸으로서 좋은지**를 본다. 정답 글자가 틀렸으면(추출 오류) 나쁨이다.
- 다른 칸 때문에 나빠진 경우(붙어 있는 칸, 같은 줄에 칸이 몰림)는 애매로 두고, 어느 칸이 문제인지 팀에서 따로 모은다.
- 망설여지면 애매. 애매는 보정 계산에서 좋음·나쁨의 분모에 들어가지 않는다.

## 4. 보정 실행 기록과 증빙

### 입력 고정

[보정 절차](selection-and-quality-gate.md#25-기준값-보정-로드맵-2단계-완료-조건)에 따라
PDF 세트를 고르고, [1절](#1-시트-만들기)의 명령으로 원본 라벨 시트와 가림 이미지를 만든다.
같은 PDF를 다시 사용했는지 확인할 수 있도록 비공개 실행 기록에 아래 값을 남긴다.

| 기록 항목 | 기록할 값 |
|---|---|
| PDF 세트 | 공개 가능한 내부 별칭, 파일 수, 별칭과 원본 파일명·SHA-256의 비공개 대응표 |
| 실행 환경 | 코드 커밋 SHA, 해당 커밋의 `uv.lock`, Python 버전 |
| 추출 및 선택 | 시트의 `extract_version`, 선택기 종류, 실제 사용한 seed와 density |
| 평가자 및 기준 | 평가자별 별칭, 평가자와 별칭의 비공개 대응표, 평가에 사용한 라벨 가이드 커밋 |
| 비교 설정 | 지표 열, 제거 비교 연산자, 사용한 순서 그대로의 임계값 후보, 평가에 사용한 명령 |

PowerShell에서 실행 버전과 PDF 해시를 확인할 수 있다. PDF 폴더를 환경 변수로 설정한
경우의 예시다. 해시 출력은 비공개 실행 기록에만 옮긴다.

```powershell
git rev-parse HEAD
git log -1 --format=%H -- docs/design/label-guide.md
uv run --locked python --version
Get-ChildItem -LiteralPath $env:CLOZE_SAMPLES_DIR -File |
    Where-Object Extension -eq '.pdf' |
    Sort-Object Name |
    Get-FileHash -Algorithm SHA256
```

### 라벨 완결성 확인과 후보 비교

독립 라벨링이 끝나면 평가자별 시트에 [1절](#1-시트-만들기)의 검사를 실행한다.
각 시트에서 `label`·`labeler`가 전부 채워졌는지, 원본과 같은 칸을 한 번씩 평가했는지
확인한다. 예를 들어 아래 명령에서 차이가 출력되면 보고서를 만들기 전에 누락·추가 행을 확인한다.

```powershell
$original = Import-Csv -Encoding UTF8 -LiteralPath 'out/labels/rule.csv'
$rated = Import-Csv -Encoding UTF8 -LiteralPath 'out/labels/rule_evaluator.csv'
Compare-Object -ReferenceObject @($original) -DifferenceObject @($rated) `
    -Property file,page,order,extract_version -CaseSensitive
```

평가자마다 [기존 후보 비교 명령](selection-and-quality-gate.md#25-기준값-보정-로드맵-2단계-완료-조건)을
같은 지표·연산자·후보로 실행한다. 각 평가자의 보고서를 따로 저장하고, 라벨이 없는 시트나
일부 칸만 평가한 시트의 결과는 비교에 사용하지 않는다. Gemini 결과 비교는 Issue #43이
`develop`에 병합되고 같은 PDF 세트와 추출 버전으로 비교용 시트를 만들 수 있을 때 진행한다.
동일한 평가자와 라벨 기준을 사용하고, 두 선택기에서 의미가 같은 지표만 직접 비교한다.

### 리뷰에 첨부할 집계

PR에는 PDF 세트 별칭·파일 수, 추출 및 실행 설정, 평가자 별칭과 아래 집계만 옮긴다.
원본 PDF, 파일명·경로·해시, 정답·문맥, 칸별 라벨 및 평가자 실제 이름은 비공개로 보관한다.
비율에는 제거 수와 전체 수를 함께 적고, 분모가 없으면 보고서의 `N/A`를 유지한다.

| 선택기 | 평가자 별칭 | 지표 | 연산자 | 후보값 | 좋음 전체 | 애매 전체 | 나쁨 전체 | 나쁜 칸 제거 수/전체 및 비율 | 좋은 칸 오제거 수/전체 및 비율 |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | |

실측을 마친 뒤 후보별 나쁜 칸 제거와 좋은 칸 오제거의 차이를 기록한다. 기준값과 선택
이유는 팀이 합의한 뒤 첨부한다. 실측 자료나 합의가 없으면 결과와 기준값은 미확정으로 표시한다.
