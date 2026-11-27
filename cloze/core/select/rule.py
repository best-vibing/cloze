"""규칙 기반 선택기. 신호별 점수를 가중합해 빈칸으로 만들 토큰을 고른다.

같은 줄에서 이어진 강조 토큰(굵게, 또는 큰 글자)은 구 하나로 묶어 칸 하나로 가린다.
"""

import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from cloze.core.filter import EDGE_PUNCTUATION, excluded_count, normalize_ko, word_key
from cloze.core.models import PageCandidates, PageTokens, Policy, Selection, Slot, Token
from cloze.core.phrase import MAX_PHRASE_TOKENS, adjacent

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
# [가설] 구 하나에 담을 최대 토큰 수. 이보다 긴 강조 구간은 용어보다 문장·제목으로 보고 묶지 않는다.
# [가설] 굵지 않아도 페이지 중앙값의 이 배수 이상인 글자 크기면 강조로 보고 구로 묶을 수 있다.
PHRASE_SIZE_RATIO = 1.2
# [가설] 이웃 토큰의 글자 크기 차이가 이 값(pt) 이하이면 같은 스타일로 본다.
PHRASE_SIZE_TOLERANCE = 0.5
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
    """칸 하나는 토큰 하나 또는 구 하나다. 구도 목표 개수와 MAX_PER_LINE에서 한 칸으로 센다.
    프리셋은 아직 concept뿐이라 preset 값으로 갈라지지 않는다."""
    target = target_count(page, candidates, policy)
    ranked = _rank(page, candidates, policy.seed, doc_stats)
    # avoid에 든 토큰을 뒤로 보내, 후보가 모자랄 때만 다시 쓰이게 한다.
    # 구는 토큰 하나라도 avoid에 들면 통째로 뒤로 간다.
    fresh = [unit for unit in ranked if not any(token.id in avoid for token in unit)]
    reused = [unit for unit in ranked if any(token.id in avoid for token in unit)]
    spans = sorted([token.id for token in unit] for unit in _pick([*fresh, *reused], target))
    return Selection(spans=spans, slots=[Slot() for _ in spans], source="rule")


def target_count(page: PageTokens, candidates: PageCandidates, policy: Policy) -> int:
    """목표 빈칸 수. 문제 영역과 비학습 영역을 뺀 단어 수에 비례한다."""
    excluded = excluded_count(candidates, "question_region") + excluded_count(
        candidates, "noncontent"
    )
    countable = len(page.tokens) - excluded
    return round(countable * policy.density / 100)


type Unit = list[Token]


def _rank(page: PageTokens, candidates: PageCandidates, seed: int, stats: DocStats) -> list[Unit]:
    """칸 후보(토큰 하나 또는 구)를 점수 순으로 돌려준다. 구의 점수는 토큰 점수의 평균이다."""
    allowed = set(candidates.candidate_ids)
    tokens = sorted((token for token in page.tokens if token.id in allowed), key=lambda t: t.id)
    if not tokens:
        return []
    noncontent = {item.id for item in candidates.excluded if item.reason == "noncontent"}
    median = statistics.median(
        token.font_size for token in page.tokens if token.id not in noncontent
    )
    units = _units(tokens, median)
    # 보안 용도가 아니라, 같은 입력에 같은 결과를 내면서 동점 순서만 정하는 난수다.
    # 구가 없는 페이지는 units가 토큰 순서 그대로라 구를 넣기 전과 같은 순서로 섞인다.
    random.Random(seed).shuffle(units)  # nosec B311
    return sorted(units, key=lambda unit: -round(_unit_score(unit, median, stats), SCORE_DIGITS))


def _unit_score(unit: Unit, median: float, stats: DocStats) -> float:
    return statistics.fmean(score_token(token, median, stats) for token in unit)


def _units(tokens: list[Token], median: float) -> list[Unit]:
    """이어진 강조 구간을 구로 묶는다. MAX_PHRASE_TOKENS보다 긴 구간은 토큰 하나씩 둔다."""
    units: list[Unit] = []
    for run in _runs(tokens, median):
        if len(run) <= MAX_PHRASE_TOKENS:
            units.append(run)
        else:
            units.extend([token] for token in run)
    return units


def _runs(tokens: list[Token], median: float) -> list[Unit]:
    """id 순서의 후보 토큰을 묶일 수 있는 구간으로 나눈다.
    후보가 아닌 토큰은 빠져 있으므로 id가 끊겨 경계가 된다."""
    runs: list[Unit] = []
    for token in tokens:
        if runs and _joins(runs[-1][-1], token, median):
            runs[-1].append(token)
        else:
            runs.append([token])
    return runs


def _joins(left: Token, right: Token, median: float) -> bool:
    """같은 줄의 바로 다음 토큰이고, 둘 다 같은 강조 스타일이며, 사이에 구를 끊는 표시가 없는가."""
    return (
        adjacent(left, right) and _same_emphasis(left, right, median) and not _breaks(left, right)
    )


def _same_emphasis(left: Token, right: Token, median: float) -> bool:
    same_style = (
        bold_signal(left) == bold_signal(right)
        and abs(left.font_size - right.font_size) <= PHRASE_SIZE_TOLERANCE
    )
    emphasized = bold_signal(left) == 1.0 or left.font_size >= median * PHRASE_SIZE_RATIO
    return same_style and emphasized


def _breaks(left: Token, right: Token) -> bool:
    """왼쪽이 문장 부호나 조사로 끝나거나 오른쪽이 여는 부호로 시작하면 구를 끊는다.
    쉼표를 넘어 묶으면 상자가 부호까지 덮고, 조사 붙은 어절은 구의 끝에만 온다
    ("데이터를 학습한다"를 막는다)."""
    word = left.text.strip(EDGE_PUNCTUATION)
    marks = tuple(EDGE_PUNCTUATION)
    return left.text.endswith(marks) or right.text.startswith(marks) or normalize_ko(word) != word


def _pick(ranked: list[Unit], target: int) -> list[Unit]:
    """단어 하나라도 이미 고른 칸과 겹치는 칸은 건너뛴다.
    구 안의 단어도 다른 칸에 또 가리지 않는다."""
    chosen: list[Unit] = []
    per_line: Counter[int] = Counter()
    seen: set[str] = set()
    for unit in ranked:
        keys = {word_key(token.text) for token in unit}
        line = unit[0].line_id
        if len(chosen) < target and per_line[line] < MAX_PER_LINE and not keys & seen:
            chosen.append(unit)
            per_line[line] += 1
            seen |= keys
    return chosen
