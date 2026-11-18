import pytest

from cloze.core.filter import normalize_ko, token_reason, word_key


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("역전파는", "역전파"),
        ("신경망이", "신경망"),
        ("학습으로부터", "학습"),
        ("CNN은", "CNN"),
        ("역전파", "역전파"),
        ("gradient", "gradient"),
    ],
)
def test_normalize_ko_strips_josa(text: str, expected: str) -> None:
    assert normalize_ko(text) == expected


@pytest.mark.parametrize("text", ["정의", "사이", "이는"])
def test_normalize_ko_keeps_short_stems(text: str) -> None:
    assert normalize_ko(text) == text


def test_word_key_ignores_case_punctuation_and_josa() -> None:
    assert word_key("Gradient,") == word_key("gradient") == "gradient"
    assert word_key("(역전파는)") == word_key("역전파를") == "역전파"


@pytest.mark.parametrize("text", ["the", "The", "with,", "것은", "그리고"])
def test_stopwords_are_excluded(text: str) -> None:
    assert token_reason(text, include_numbers=False) == "stopword"


@pytest.mark.parametrize("text", ["ab", "x", "AI", "물"])
def test_short_words_are_excluded(text: str) -> None:
    assert token_reason(text, include_numbers=False) == "too_short"


def test_long_english_word_is_excluded() -> None:
    assert token_reason("a" * 26, include_numbers=False) == "too_long"
    assert token_reason("a" * 25, include_numbers=False) is None


@pytest.mark.parametrize("text", ["over-", "trans­"])
def test_trailing_hyphen_is_excluded(text: str) -> None:
    assert token_reason(text, include_numbers=False) == "trailing_hyphen"


@pytest.mark.parametrize("text", ["x=1", "H2O", "GPT4", "cross-entropy", "a/b", "•", "()"])
def test_mixed_symbols_and_digits_are_excluded(text: str) -> None:
    assert token_reason(text, include_numbers=False) == "has_symbol"
    assert token_reason(text, include_numbers=True) == "has_symbol"


@pytest.mark.parametrize("text", ["42", "3.14", "1,000", "2024."])
def test_pure_numbers_follow_policy(text: str) -> None:
    assert token_reason(text, include_numbers=False) == "number"
    assert token_reason(text, include_numbers=True) is None


@pytest.mark.parametrize("text", ["cell.", "(Mitochondria),", "역전파는", "CNN은", "신경망"])
def test_ordinary_words_stay_candidates(text: str) -> None:
    assert token_reason(text, include_numbers=False) is None
