"""빈칸 후보를 거른다. 기준은 docs/design/selection-and-quality-gate.md 2.1절이다."""

import math
import re
from collections import defaultdict

from cloze.core.models import (
    Excluded,
    ExcludeReason,
    PageCandidates,
    PageTokens,
    Policy,
    Token,
)

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

# [가설] 전체 페이지의 이 비율 이상에서 반복되면 머리글·바닥글로 본다. 최소 2쪽이다.
REPEAT_PAGE_RATIO = 0.5
MIN_REPEAT_PAGES = 2
# [가설] 세로 위치(0~1) 차이가 이 값 이하이면 "비슷한 위치"로 본다.
REPEAT_TOP_TOLERANCE = 0.02

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_HANGUL = re.compile(r"[가-힣]")
_DIGITS = re.compile(r"\d+")

type _Line = tuple[int, float, list[int]]  # 페이지 번호, 줄의 top, 토큰 id들


def filter_candidates(pages: list[PageTokens], policy: Policy) -> list[PageCandidates]:
    repeated = _repeated_ids(pages)
    return [_page_candidates(page, repeated, policy) for page in pages]


def _page_candidates(
    page: PageTokens, repeated: set[tuple[int, int]], policy: Policy
) -> PageCandidates:
    candidate_ids: list[int] = []
    excluded: list[Excluded] = []
    for token in page.tokens:
        reason = _reason(page.page.page_no, token, repeated, policy)
        if reason is None:
            candidate_ids.append(token.id)
        else:
            excluded.append(Excluded(id=token.id, reason=reason))
    return PageCandidates(page_no=page.page.page_no, candidate_ids=candidate_ids, excluded=excluded)


def _reason(
    page_no: int, token: Token, repeated: set[tuple[int, int]], policy: Policy
) -> ExcludeReason | None:
    if (page_no, token.id) in repeated:
        return "header_footer"
    return token_reason(token.text, policy.include_numbers)


def _repeated_ids(pages: list[PageTokens]) -> set[tuple[int, int]]:
    """여러 페이지의 비슷한 위치에 반복되는 줄의 (페이지 번호, 토큰 id)를 모은다."""
    needed = max(MIN_REPEAT_PAGES, math.ceil(len(pages) * REPEAT_PAGE_RATIO))
    lines_by_text: dict[str, list[_Line]] = defaultdict(list)
    for page in pages:
        for text, line in _lines(page):
            lines_by_text[text].append(line)
    repeated: set[tuple[int, int]] = set()
    for lines in lines_by_text.values():
        cluster = _largest_cluster(lines)
        if len({page_no for page_no, _, _ in cluster}) >= needed:
            repeated |= {(page_no, id_) for page_no, _, ids in cluster for id_ in ids}
    return repeated


def _lines(page: PageTokens) -> list[tuple[str, _Line]]:
    tokens_by_line: dict[int, list[Token]] = defaultdict(list)
    for token in page.tokens:
        tokens_by_line[token.line_id].append(token)
    return [_line(page.page.page_no, tokens) for tokens in tokens_by_line.values()]


def _line(page_no: int, tokens: list[Token]) -> tuple[str, _Line]:
    # 쪽 번호처럼 숫자만 바뀌는 줄도 같은 글로 보도록 숫자를 가린다.
    text = _DIGITS.sub("#", " ".join(token.text for token in tokens)).casefold()
    top = min(token.bbox[1] for token in tokens)
    return text, (page_no, top, [token.id for token in tokens])


def _largest_cluster(lines: list[_Line]) -> list[_Line]:
    """줄마다 기준으로 삼아 보고, 위치가 비슷한 줄이 가장 많은 묶음을 돌려준다."""
    clusters = (
        [line for line in lines if abs(line[1] - anchor[1]) <= REPEAT_TOP_TOLERANCE]
        for anchor in lines
    )
    return max(clusters, key=len)


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


def strip_punctuation(text: str) -> str:
    """정답에 쓸 글자: 앞뒤 문장 부호를 뗀다. 떼고 나서 아무것도 남지 않으면 원문 그대로다."""
    return text.strip(EDGE_PUNCTUATION) or text


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
