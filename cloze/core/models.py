"""단계 사이 데이터 모양. 기준은 docs/design/data-contract.md 1절이다."""

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1
# [가설] concept 프리셋의 단어 100개당 빈칸 수. 문서에 값이 없어 임시로 정했다.
DEFAULT_DENSITY = 5.0

type BBox = tuple[float, float, float, float]
type ExcludeReason = Literal[
    "stopword",
    "too_short",
    "too_long",
    "trailing_hyphen",
    "has_symbol",
    "number",
    "header_footer",
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
    type: Literal["text", "number", "term"] = "term"
    hint: str | None = None
    variants: list[str] = Field(default_factory=list)


class Selection(BaseModel):
    spans: list[list[int]]
    slots: list[Slot]
    source: Literal["gemini", "rule"]
