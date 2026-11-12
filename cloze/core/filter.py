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
# [가설] 바닥글 줄 안에서 토큰 사이 가로 간격이 글자 크기의 이 배수를 넘으면 다른 구절로 본다.
PHRASE_GAP_RATIO = 1.5
# 토큰 두 개 이상인 구절만 다른 곳과 맞춘다. 한 단어("coding")로 맞추면 본문의 정상 단어까지 빠진다.
MIN_PHRASE_TOKENS = 2

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_HANGUL = re.compile(r"[가-힣]")
_DIGITS = re.compile(r"\d+")

type _Line = tuple[int, float, list[int]]  # 페이지 번호, 줄의 top, 토큰 id들
type _Key = tuple[int, int]  # 페이지 번호, 토큰 id
type _Phrase = tuple[str, ...]  # 구절을 이루는 단어 key들


def filter_candidates(pages: list[PageTokens], policy: Policy) -> list[PageCandidates]:
    doc_reasons = _document_reasons(pages)
    return [_page_candidates(page, doc_reasons, policy) for page in pages]


def _page_candidates(
    page: PageTokens, doc_reasons: dict[_Key, ExcludeReason], policy: Policy
) -> PageCandidates:
    candidate_ids: list[int] = []
    excluded: list[Excluded] = []
    for token in page.tokens:
        reason = _reason(page.page.page_no, token, doc_reasons, policy)
        if reason is None:
            candidate_ids.append(token.id)
        else:
            excluded.append(Excluded(id=token.id, reason=reason))
    return PageCandidates(page_no=page.page.page_no, candidate_ids=candidate_ids, excluded=excluded)


def _reason(
    page_no: int, token: Token, doc_reasons: dict[_Key, ExcludeReason], policy: Policy
) -> ExcludeReason | None:
    doc_reason = doc_reasons.get((page_no, token.id))
    return doc_reason or token_reason(token.text, policy.include_numbers)


def _document_reasons(pages: list[PageTokens]) -> dict[_Key, ExcludeReason]:
    """문서 전체를 봐야 아는 제외 사유. 반복 머리글·바닥글이 먼저이고, 다른 곳에서 줄 전체가
    바닥글 구절로만 이루어진 줄(표지의 과목명·이름 등)의 토큰은 footer_phrase다."""
    repeated = _repeated_ids(pages)
    reasons: dict[_Key, ExcludeReason] = dict.fromkeys(repeated, "header_footer")
    phrases = _footer_phrases(pages, repeated)
    for key in _phrase_ids(pages, phrases):
        reasons.setdefault(key, "footer_phrase")
    return reasons


def _repeated_ids(pages: list[PageTokens]) -> set[_Key]:
    """여러 페이지의 비슷한 위치에 반복되는 줄의 (페이지 번호, 토큰 id)를 모은다.
    기준을 넘는 위치 묶음은 모두 뺀다. 두 장 모아찍기 PDF는 바닥글이 한 쪽에 두 번 나온다."""
    needed = max(MIN_REPEAT_PAGES, math.ceil(len(pages) * REPEAT_PAGE_RATIO))
    lines_by_text: dict[str, list[_Line]] = defaultdict(list)
    for page in pages:
        for text, line in _lines(page):
            lines_by_text[text].append(line)
    repeated: set[_Key] = set()
    for lines in lines_by_text.values():
        for cluster in _clusters(lines):
            if len({page_no for page_no, _, _ in cluster}) >= needed:
                repeated |= {(page_no, id_) for page_no, _, ids in cluster for id_ in ids}
    return repeated


def _line_tokens(page: PageTokens) -> list[list[Token]]:
    """줄마다 토큰을 읽는 순서대로 묶는다."""
    tokens_by_line: dict[int, list[Token]] = defaultdict(list)
    for token in page.tokens:
        tokens_by_line[token.line_id].append(token)
    return list(tokens_by_line.values())


def _lines(page: PageTokens) -> list[tuple[str, _Line]]:
    return [_line(page.page.page_no, tokens) for tokens in _line_tokens(page)]


def _line(page_no: int, tokens: list[Token]) -> tuple[str, _Line]:
    # 쪽 번호처럼 숫자만 바뀌는 줄도 같은 글로 보도록 숫자를 가린다.
    text = _DIGITS.sub("#", " ".join(token.text for token in tokens)).casefold()
    top = min(token.bbox[1] for token in tokens)
    return text, (page_no, top, [token.id for token in tokens])


def _clusters(lines: list[_Line]) -> list[list[_Line]]:
    """줄마다 기준으로 삼아, 위치가 비슷한 줄의 묶음을 하나씩 돌려준다. 묶음이 겹쳐도 된다."""
    return [
        [line for line in lines if abs(line[1] - anchor[1]) <= REPEAT_TOP_TOLERANCE]
        for anchor in lines
    ]


def _footer_phrases(pages: list[PageTokens], repeated: set[_Key]) -> set[_Phrase]:
    """머리글·바닥글로 확인된 줄의 문구를 구절로 모은다."""
    phrases: set[_Phrase] = set()
    for page in pages:
        for tokens in _line_tokens(page):
            if all((page.page.page_no, token.id) in repeated for token in tokens):
                phrases |= set(_split_phrases(page, tokens))
    return phrases


def _split_phrases(page: PageTokens, tokens: list[Token]) -> list[_Phrase]:
    """줄을 구절로 나눠, 토큰 두 개 이상인 구절만 돌려준다."""
    return [phrase for phrase in _segments(page, tokens) if len(phrase) >= MIN_PHRASE_TOKENS]


def _segments(page: PageTokens, tokens: list[Token]) -> list[_Phrase]:
    """줄을 구분 기호나 큰 간격에서 나눈다. 구분 기호는 빼고, 빈 구간은 버린다."""
    phrases: list[list[str]] = [[]]
    previous: Token | None = None
    for token in tokens:
        if _starts_new_phrase(page, previous, token):
            phrases.append([])
        if not _is_separator(token.text):
            phrases[-1].append(word_key(token.text))
        previous = token
    return [tuple(words) for words in phrases if words]


def _starts_new_phrase(page: PageTokens, previous: Token | None, token: Token) -> bool:
    if _is_separator(token.text):
        return True
    if previous is None:
        return False
    gap_pt = (token.bbox[0] - previous.bbox[2]) * page.page.width_pt
    return gap_pt > token.font_size * PHRASE_GAP_RATIO


def _is_separator(text: str) -> bool:
    return not any(char.isalnum() for char in text)


def _phrase_ids(pages: list[PageTokens], phrases: set[_Phrase]) -> set[_Key]:
    """줄 전체가 바닥글 구절로만 이루어진 줄의 토큰(구분 기호 제외)을 모은다."""
    matched: set[_Key] = set()
    for page in pages:
        for tokens in _line_tokens(page):
            if _is_footer_phrase_line(page, tokens, phrases):
                no = page.page.page_no
                matched |= {(no, t.id) for t in tokens if not _is_separator(t.text)}
    return matched


def _is_footer_phrase_line(page: PageTokens, tokens: list[Token], phrases: set[_Phrase]) -> bool:
    """줄을 바닥글과 같은 기준으로 나눈 구절이 모두 바닥글 구절이면 참이다. 본문 문장 속에
    같은 구절이 나오면("Vibe coding is a way …") 학습 내용이므로 거짓이다."""
    segments = _segments(page, tokens)
    return bool(segments) and all(segment in phrases for segment in segments)


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
