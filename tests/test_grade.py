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
    ("세포소기관", AnswerKey("세포 소기관"), "near", "공백을 모두 지우지는 않음: 공백 하나 빠짐"),
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
    (".5", AnswerKey("0.5", type="number"), "correct", "앞의 0을 생략한 소수"),
    ("COVID-19", AnswerKey("covid19"), "correct", "단어 안 붙임표는 지움"),
    ("3.141", AnswerKey("3.14", type="term"), "wrong", "숫자 칸이 아니면 숫자 비교 안 함"),
    ("세 개", AnswerKey("3", type="number"), "wrong", "숫자로 읽을 수 없는 입력"),
    # 4단계: near
    ("Golgy", AnswerKey("Golgi"), "near", "5자 이상, 한 글자 바꿈"),
    ("미토콘드리", AnswerKey("미토콘드리아"), "near", "한 글자 빠짐"),
    ("ATB", AnswerKey("ATP"), "wrong", "5자 미만은 near 없음"),
    ("Golgyy", AnswerKey("Golgi"), "wrong", "편집 거리 2"),
    ("미토콘드리는", AnswerKey("미토콘드리아"), "near", "'아'를 '는'으로 바꿈: 거리 1"),
    ("미토콘드리에는", AnswerKey("미토콘드리아"), "wrong", "near는 조사를 떼지 않은 입력으로 봄"),
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
