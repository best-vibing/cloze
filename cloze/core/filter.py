"""빈칸 후보를 거른다. 기준은 docs/design/selection-and-quality-gate.md 2.1절이다."""

import re

from cloze.core.models import ExcludeReason

STOPWORDS_EN = frozenset(
    "about above after all also and any are because been before being but can could did does "
    "during each few for from had has have her here him his how into its may more most much "
    "not now off one only other our out over should some such than that the their them then "
    "there these they this those through too under until upon very was were what when where "
    "which while who whom why will with would you your".split()
)
STOPWORDS_KO = frozenset(
    "것 것은 것이 것을 수 등 및 또는 그리고 그러나 하지만 또한 이것 그것 저것 이는 그는 이런 그런 "
    "저런 있다 없다 하다 된다 한다 이다 있는 없는 하는 되는 같은 때문 위해 통해 대해 따라".split()
)
# 긴 조사를 먼저 떼도록 길이순으로 둔다.
JOSA_KO = tuple(
    sorted(
        "으로부터 에서부터 에게서 이라는 으로서 으로써 라는 에서 에게 까지 부터 으로 로서 로써 "
        "보다 처럼 마다 조차 이나 이며 은 는 이 가 을 를 의 에 와 과 도 만 로".split(),
        key=len,
        reverse=True,
    )
)
MIN_STEM_KO = 2  # 조사를 뗀 뒤 2자 이상 남을 때만 뗀다("정의", "사이" 보호).
MIN_LEN_KO = 2
MIN_LEN_EN = 3
MAX_LEN_EN = 25
HYPHENS = ("-", "‐", "‑", "­")
# 문장 부호는 토큰의 앞뒤에서만 떼고 판정한다. 그렇지 않으면 문장 끝 단어가 모두 제외된다.
EDGE_PUNCTUATION = ".,;:!?\"'()[]{}<>“”‘’·…「」『』"

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_HANGUL = re.compile(r"[가-힣]")


def normalize_ko(text: str) -> str:
    """단어 끝의 흔한 조사를 뗀다. 비교에만 쓰고, 원문 토큰은 바꾸지 않는다."""
    for josa in JOSA_KO:
        stem = text.removesuffix(josa)
        if stem != text and len(stem) >= MIN_STEM_KO:
            return stem
    return text


def word_key(text: str) -> str:
    """같은 단어인지 비교할 때 쓰는 값: 앞뒤 부호와 조사를 떼고 대소문자를 없앤다."""
    return normalize_ko(text.strip(EDGE_PUNCTUATION)).casefold()


def token_reason(text: str, include_numbers: bool) -> ExcludeReason | None:
    """토큰 하나만 보고 제외 사유를 돌려준다. 후보로 둘 수 있으면 None이다."""
    word = text.strip(EDGE_PUNCTUATION)
    if _NUMBER.fullmatch(word):
        return _number_reason(include_numbers)
    return _shape_reason(word) or _word_reason(word)


def _number_reason(include_numbers: bool) -> ExcludeReason | None:
    return None if include_numbers else "number"


def _shape_reason(word: str) -> ExcludeReason | None:
    if word.endswith(HYPHENS):
        return "trailing_hyphen"
    # 글자가 아닌 것이 하나라도 있으면(숫자·기호 혼합) 제외한다. 빈 문자열도 여기서 걸린다.
    return None if word.isalpha() else "has_symbol"


def _word_reason(word: str) -> ExcludeReason | None:
    key = normalize_ko(word).casefold()
    if key in STOPWORDS_EN or key in STOPWORDS_KO:
        return "stopword"
    return _length_reason(key)


def _length_reason(key: str) -> ExcludeReason | None:
    if _HANGUL.search(key):
        return "too_short" if len(key) < MIN_LEN_KO else None
    if len(key) < MIN_LEN_EN:
        return "too_short"
    return "too_long" if len(key) > MAX_LEN_EN else None
