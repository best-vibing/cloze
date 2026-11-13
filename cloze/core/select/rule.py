"""규칙 기반 선택기. 신호별 점수를 가중합해 빈칸으로 만들 토큰을 고른다."""

import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from cloze.core.filter import word_key
from cloze.core.models import PageCandidates, PageTokens, Policy, Selection, Slot, Token

# [가설] 신호별 가중치. 합은 1이다. 실데이터로 보정한다(selection-and-quality-gate.md 2.5절).
WEIGHT_SIZE = 0.35
WEIGHT_BOLD = 0.25
WEIGHT_HEADING = 0.20
WEIGHT_FREQUENCY = 0.20
# [가설] 페이지 중앙값의 이 배수 이상이면 글자 크기 신호가 최대다.
SIZE_RATIO_CAP = 2.0
# [가설] 문서의 줄 중 글자 크기 상위 이 비율을 제목급으로 본다.
HEADING_LINE_RATIO = 0.1
BOLD_MARKERS = ("BOLD", "BLACK", "HEAVY")
MAX_PER_LINE = 2
# 부동소수 오차가 동점을 깨지 않도록 점수를 이 자릿수로 반올림해 비교한다.
SCORE_DIGITS = 9


@dataclass(frozen=True)
class DocStats:
    frequency: Counter[str]
    heading_words: frozenset[str]


def build_doc_stats(pages: list[PageTokens], candidates: list[PageCandidates]) -> DocStats:
    """후보 토큰만 센다. 불용어와 머리글 반복이 점수를 부풀리지 않게 하기 위해서다."""
    kept_ids = {item.page_no: set(item.candidate_ids) for item in candidates}
    lines: dict[tuple[int, int], list[Token]] = defaultdict(list)
    for page in pages:
        for token in page.tokens:
            if token.id in kept_ids.get(page.page.page_no, set()):
                lines[(page.page.page_no, token.line_id)].append(token)
    words = (word_key(token.text) for tokens in lines.values() for token in tokens)
    return DocStats(frequency=Counter(words), heading_words=_heading_words(lines.values()))


def _heading_words(lines: Iterable[list[Token]]) -> frozenset[str]:
    groups = list(lines)
    if not groups:
        return frozenset()
    sizes = [max(token.font_size for token in tokens) for tokens in groups]
    threshold = _heading_threshold(sizes)
    return frozenset(
        word_key(token.text)
        for tokens, size in zip(groups, sizes, strict=True)
        if size >= threshold
        for token in tokens
    )


def _heading_threshold(sizes: list[float]) -> float:
    """상위 비율의 경계 크기. 중앙값 이하이면 제목급이 없다고 보고 무한대를 돌려준다."""
    top_count = math.ceil(len(sizes) * HEADING_LINE_RATIO)
    threshold = sorted(sizes, reverse=True)[top_count - 1]
    return threshold if threshold > statistics.median(sizes) else math.inf


def size_signal(token: Token, page_median: float) -> float:
    ratio = token.font_size / page_median
    return min(max((ratio - 1.0) / (SIZE_RATIO_CAP - 1.0), 0.0), 1.0)


def bold_signal(token: Token) -> float:
    name = token.fontname.upper()
    return 1.0 if any(marker in name for marker in BOLD_MARKERS) else 0.0


def heading_signal(token: Token, stats: DocStats) -> float:
    return 1.0 if word_key(token.text) in stats.heading_words else 0.0


def frequency_signal(token: Token, stats: DocStats) -> float:
    """한 번만 나오면 0, 문서에서 가장 많이 나온 단어면 1이다."""
    count = stats.frequency[word_key(token.text)]
    highest = max(stats.frequency.values(), default=0)
    if count <= 1 or highest <= 1:
        return 0.0
    return math.log(count) / math.log(highest)


def score_token(token: Token, page_median: float, stats: DocStats) -> float:
    return (
        WEIGHT_SIZE * size_signal(token, page_median)
        + WEIGHT_BOLD * bold_signal(token)
        + WEIGHT_HEADING * heading_signal(token, stats)
        + WEIGHT_FREQUENCY * frequency_signal(token, stats)
    )


def select_rule(
    page: PageTokens,
    candidates: PageCandidates,
    policy: Policy,
    avoid: set[int],
    doc_stats: DocStats,
) -> Selection:
    """칸 하나는 토큰 하나다. 프리셋은 아직 concept뿐이라 preset 값으로 갈라지지 않는다."""
    target = round(len(page.tokens) * policy.density / 100)
    ranked = _rank(page, candidates, policy.seed, doc_stats)
    # avoid에 든 토큰을 뒤로 보내, 후보가 모자랄 때만 다시 쓰이게 한다.
    fresh = [token for token in ranked if token.id not in avoid]
    reused = [token for token in ranked if token.id in avoid]
    ids = sorted(token.id for token in _pick([*fresh, *reused], target))
    return Selection(spans=[[id_] for id_ in ids], slots=[Slot() for _ in ids], source="rule")


def _rank(page: PageTokens, candidates: PageCandidates, seed: int, stats: DocStats) -> list[Token]:
    allowed = set(candidates.candidate_ids)
    tokens = [token for token in page.tokens if token.id in allowed]
    if not tokens:
        return []
    median = statistics.median(token.font_size for token in page.tokens)
    # 보안 용도가 아니라, 같은 입력에 같은 결과를 내면서 동점 순서만 정하는 난수다.
    random.Random(seed).shuffle(tokens)  # nosec B311
    return sorted(tokens, key=lambda token: -round(score_token(token, median, stats), SCORE_DIGITS))


def _pick(ranked: list[Token], target: int) -> list[Token]:
    chosen: list[Token] = []
    per_line: Counter[int] = Counter()
    seen: set[str] = set()
    for token in ranked:
        key = word_key(token.text)
        if len(chosen) < target and per_line[token.line_id] < MAX_PER_LINE and key not in seen:
            chosen.append(token)
            per_line[token.line_id] += 1
            seen.add(key)
    return chosen
