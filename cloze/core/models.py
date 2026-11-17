"""단계 사이 데이터 모양. 기준은 docs/design/data-contract.md 1절이다."""

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 2
# [가설] concept 프리셋의 단어 100개당 빈칸 수. 문서에 값이 없어 임시로 정했다.
DEFAULT_DENSITY = 5.0

type BBox = tuple[float, float, float, float]
# type 문으로 만들면 JSON 스키마에 $ref가 생겨 Slot 스키마가 달라진다. 대입으로 두면 펼쳐 넣는다.
BlankType = Literal["text", "number", "term"]
type ExcludeReason = Literal[
    "stopword",
    "too_short",
    "too_long",
    "trailing_hyphen",
    "has_symbol",
    "number",
    "header_footer",
    "footer_phrase",
    "question_region",
]


class Policy(BaseModel):
    preset: Literal["auto", "vocab", "concept", "number", "definition"] = "auto"
    density: float = Field(default=DEFAULT_DENSITY, ge=0)
    language: Literal["auto", "en", "ko"] = "auto"
    include_numbers: bool = False
    seed: int


class Token(BaseModel):
    id: int
    text: str
    bbox: BBox
    # 앞뒤 문장 부호를 뺀 글자들의 bbox. 뗄 부호가 없으면 None이고, 그때는 bbox와 같다.
    word_bbox: BBox | None = None
    line_id: int
    font_size: float
    fontname: str


class PageInfo(BaseModel):
    page_no: int
    width_pt: float
    height_pt: float
    source: Literal["textlayer", "ocr"] = "textlayer"


class PageTokens(BaseModel):
    page: PageInfo
    tokens: list[Token]


class Excluded(BaseModel):
    id: int
    reason: ExcludeReason


class PageCandidates(BaseModel):
    page_no: int
    candidate_ids: list[int]
    excluded: list[Excluded]


class Slot(BaseModel):
    type: BlankType = "term"
    hint: str | None = None
    variants: list[str] = Field(default_factory=list)


class Selection(BaseModel):
    spans: list[list[int]]
    slots: list[Slot]
    source: Literal["gemini", "rule"]


class BlankStyle(BaseModel):
    """입력칸 표시 정보. 뜻은 docs/design/grading-and-overlay.md 3.1이다."""

    font_size_ratio: float
    font_color: str
    bg_color: str


class Blank(BaseModel):
    """페이지 결과의 빈칸 하나(계약 1.4). id·set_id·page_no는 DB 행이 갖는다."""

    token_ids: list[int] = Field(min_length=1)
    bbox: BBox
    type: BlankType
    hint: str | None
    style: BlankStyle
    answer: str
    variants: list[str]
    # [가설] 계약 1.4 예시에는 null만 있어 묶음 번호를 정수로 두었다. 같은 페이지 안에서만 묶는다.
    answer_group: int | None
    # 페이지 안 읽는 순서(줄 → x좌표). 계약 4절에는 있고 1.4 예시에는 없다.
    order: int
