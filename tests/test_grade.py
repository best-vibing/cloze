"""cloze.core.grade 판정 예시 표. 기준: docs/design/grading-and-overlay.md 1절."""

import pytest

from cloze.core.grade import AnswerKey, Verdict, edit_distance, grade, judge, normalize

# (입력, 정답 정보, 기대 판정, 무엇을 확인하나)
JUDGE_CASES: list[tuple[str, AnswerKey, Verdict, str]] = [
    # 1단계: 정규화
    ("mitochondria", AnswerKey("Mitochondria"), "correct", "대소문자 무시"),
    ("ＡＴＰ", AnswerKey("ATP"), "correct", "NFKC: 전각 글자"),
    ("  세포   소기관 ", AnswerKey("세포 소기관"), "correct", "앞뒤·연속 공백 정리"),
    ("ATP!", AnswerKey("ATP"), "correct", "문장부호 제거"),
    ("세포소기관", AnswerKey("세포 소기관"), "correct", "공백을 모두 지워도 같으면 맞음"),
    # 한국어 조사
    ("미토콘드리아는", AnswerKey("미토콘드리아"), "correct", "입력 끝 조사"),
    ("세포막", AnswerKey("세포막은"), "correct", "정답 어절 끝 조사"),
    ("세포막으로부터", AnswerKey("세포막"), "correct", "긴 조사(normalize_ko 목록)"),
    ("사이", AnswerKey("사"), "wrong", "조사를 떼고 1자만 남으면 떼지 않음"),
    ("1665년", AnswerKey("1665", type="number"), "wrong", "'년'은 조사 목록에 없음"),
    # 2단계: 변형 목록
    ("ER", AnswerKey("endoplasmic reticulum", variants=("ER",)), "correct", "약어 변형"),
    ("소포체", AnswerKey("endoplasmic reticulum", variants=("ER", "소포체")), "correct", "동의어"),
    # 3단계: 숫자
    ("1,665", AnswerKey("1665", type="number"), "correct", "천 단위 쉼표"),
    ("3.141", AnswerKey("3.14", type="number"), "correct", "유효숫자 범위 안(±0.005)"),
    ("3.15", AnswerKey("3.14", type="number"), "wrong", "유효숫자 범위 밖"),
    ("31.4", AnswerKey("3.14", type="number"), "wrong", "소수점을 지워 같아지지 않음"),
    ("273", AnswerKey("-273", type="number"), "wrong", "음수 부호를 지우지 않음"),
    ("-273.15", AnswerKey("-273.15", type="number"), "correct", "음수 소수"),
    ("-273", AnswerKey("\u2212273", type="number"), "correct", "정답의 빼기 기호(U+2212)를 -로 봄"),
    (".5", AnswerKey("0.5", type="number"), "correct", "앞의 0을 생략한 소수"),
    ("COVID-19", AnswerKey("covid19"), "correct", "단어 안 붙임표는 지움"),
    ("3.141", AnswerKey("3.14", type="term"), "wrong", "숫자 칸이 아니면 숫자 비교 안 함"),
    ("세 개", AnswerKey("3", type="number"), "wrong", "숫자로 읽을 수 없는 입력"),
    # 4단계: near
    ("Golgy", AnswerKey("Golgi"), "near", "5자 이상, 한 글자 바꿈"),
    ("미토콘드리", AnswerKey("미토콘드리아"), "near", "한 글자 빠짐"),
    ("ATB", AnswerKey("ATP"), "wrong", "5자 미만은 near 없음"),
    ("900000", AnswerKey("100000", type="number"), "wrong", "숫자 칸은 near 없음: 한 자리 차이"),
    ("12355", AnswerKey("12345", type="number"), "wrong", "숫자 칸은 near 없음: 범위 밖"),
    ("Golgy", AnswerKey("Golgi apparatus", variants=("Golgi",)), "wrong", "near는 변형을 안 봄"),
    ("Golgyy", AnswerKey("Golgi"), "wrong", "편집 거리 2"),
    ("미토콘드리는", AnswerKey("미토콘드리아"), "near", "'아'를 '는'으로 바꿈: 거리 1"),
    ("미토콘드리에는", AnswerKey("미토콘드리아"), "wrong", "near는 조사를 떼지 않은 입력으로 봄"),
    # 1단계: 공백을 모두 지운 비교(#47 후속). 구 정답(#124)과 한국어 띄어쓰기
    (
        "oxidativephosphorylation",
        AnswerKey("oxidative phosphorylation"),
        "correct",
        "구 정답을 붙여 씀",
    ),
    (
        "oxidative-phosphorylation",
        AnswerKey("oxidative phosphorylation"),
        "correct",
        "구 정답을 붙임표로 이음",
    ),
    (
        "Oxidative  Phosphorylation",
        AnswerKey("oxidative phosphorylation"),
        "correct",
        "대소문자·연속 공백",
    ),
    (
        "oxidative phosphorylation",
        AnswerKey("oxidativephosphorylation"),
        "correct",
        "정답이 붙여 쓴 형태",
    ),
    (
        "oxidativ ephosphorylation",
        AnswerKey("oxidative phosphorylation"),
        "correct",
        "글자는 같고 공백 자리만 다름",
    ),
    (
        "oxidativ phosphorylation",
        AnswerKey("oxidative phosphorylation"),
        "near",
        "오타 한 글자는 지금처럼 near",
    ),
    (
        "oxidativphosphorylation",
        AnswerKey("oxidative phosphorylation"),
        "wrong",
        "near는 공백을 지우지 않은 입력으로 잼: 거리 2",
    ),
    ("데이터분석", AnswerKey("데이터 분석"), "correct", "띄어쓰기 빠짐"),
    ("데이터 분석", AnswerKey("데이터분석"), "correct", "띄어쓰기 더함"),
    ("데이터분석을", AnswerKey("데이터 분석"), "correct", "공백을 지운 뒤 조사를 뗌"),
    ("데이터 분석을", AnswerKey("데이터분석"), "correct", "조사가 붙고 띄어 씀"),
    ("세포 막", AnswerKey("세포막"), "correct", "5자 미만이라 near가 없던 짧은 정답"),
    ("tca회로", AnswerKey("TCA cycle", variants=("TCA 회로",)), "correct", "변형에도 적용"),
    ("3 14", AnswerKey("3.14", type="number"), "wrong", "숫자 칸은 글자 비교에서 공백을 안 지움"),
    (
        "3 14",
        AnswerKey("314", type="number"),
        "correct",
        "숫자의 공백은 3단계가 지움(1 000 같은 천 단위)",
    ),
    ("   ", AnswerKey("a b"), "wrong", "공백만 있는 입력"),
    # 빈 입력
    ("", AnswerKey("ATP"), "wrong", "빈 입력"),
    ("!!", AnswerKey("ATP"), "wrong", "부호만 있는 입력"),
]


@pytest.mark.parametrize(
    ("given", "key", "expected"),
    [case[:3] for case in JUDGE_CASES],
    ids=[case[3] for case in JUDGE_CASES],
)
def test_judge(given: str, key: AnswerKey, expected: Verdict) -> None:
    assert judge(given, key) == expected


def test_normalize_keeps_punctuation_between_digits() -> None:
    assert normalize("3.14") == "3.14"
    assert normalize("v1.2!") == "v1.2"


def test_edit_distance() -> None:
    assert edit_distance("", "abc") == 3
    assert edit_distance("kitten", "sitting") == 3


def test_grade_score_counts_correct_only() -> None:
    keys = {"a": AnswerKey("ATP"), "b": AnswerKey("Golgi"), "c": AnswerKey("리보솜")}
    result = grade({"a": "atp", "b": "Golgy"}, keys)
    assert result.results == {"a": "correct", "b": "near", "c": "wrong"}
    assert result.score == pytest.approx(1 / 3)


def test_grade_empty_set() -> None:
    assert grade({}, {}).score == 0.0


def test_answer_group_allows_swapped_inputs() -> None:
    keys = {
        "a": AnswerKey("mitochondria", answer_group=1),
        "b": AnswerKey("ribosome", answer_group=1),
        "c": AnswerKey("ribosome"),
    }
    result = grade({"a": "ribosome", "b": "mitochondria", "c": "mitochondria"}, keys)
    assert result.results == {"a": "correct", "b": "correct", "c": "wrong"}


def test_answer_group_does_not_reuse_one_answer() -> None:
    keys = {"a": AnswerKey("X", answer_group="g"), "b": AnswerKey("Y", answer_group="g")}
    result = grade({"a": "Y", "b": "Y"}, keys)
    assert result.results == {"a": "wrong", "b": "correct"}
    assert result.score == 0.5


def test_answer_group_ignores_spaces() -> None:
    keys = {
        "a": AnswerKey("oxidative phosphorylation", answer_group=1),
        "b": AnswerKey("데이터 분석", answer_group=1),
    }
    g = grade({"a": "데이터분석", "b": "oxidative-phosphorylation"}, keys)
    assert g.results == {"a": "correct", "b": "correct"}
