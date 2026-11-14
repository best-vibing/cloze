"""tests/fixtures/의 fixture PDF(sample, render, twoup, questions)를 표준 라이브러리만으로 만든다.

실행: uv run python tests/fixtures/make_fixtures.py
"""

from pathlib import Path

type Run = tuple[str, int, int, int, str]  # 폰트, 크기, x, y, 글

REGULAR, BOLD, KOREAN = "R", "B", "K"
FONTS = (
    (REGULAR, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
    (BOLD, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"),
    (
        KOREAN,
        b"<< /Type /Font /Subtype /Type0 /BaseFont /HYGoThic-Medium /Encoding /UniKS-UTF16-H "
        b"/DescendantFonts [<< /Type /Font /Subtype /CIDFontType0 /BaseFont /HYGoThic-Medium "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Korea1) /Supplement 2 >> "
        b"/FontDescriptor << /Type /FontDescriptor /FontName /HYGoThic-Medium /Flags 6 "
        b"/FontBBox [-200 -200 1000 1000] /ItalicAngle 0 /Ascent 880 /Descent -120 "
        b"/CapHeight 700 /StemV 80 >> >>] >>",
    ),
)


def _frame(page_no: int) -> list[Run]:
    return [
        (REGULAR, 10, 72, 760, "Cloze Sample Handbook"),
        (REGULAR, 10, 72, 30, f"Page {page_no} of 3"),
    ]


PAGES: list[list[Run]] = [
    [
        (BOLD, 24, 72, 700, "Gradient Descent"),
        (REGULAR, 12, 72, 660, "The optimizer updates weights with the learning rate on each"),
        (REGULAR, 12, 72, 640, "training step, and the loss shrinks until convergence appears."),
        (REGULAR, 12, 72, 620, "A batch of samples drives each weights update during an epoch"),
        (REGULAR, 12, 72, 600, "so noisy gradients still move toward a useful minimum of loss."),
        (BOLD, 12, 72, 580, "Momentum"),
        (REGULAR, 12, 140, 580, "smooths each step, while weights decay limits over-"),
        (REGULAR, 12, 72, 560, "fitting. See x=1 and 2024 for the version we use in class."),
    ],
    [
        (BOLD, 24, 72, 700, "Backpropagation"),
        (REGULAR, 12, 72, 660, "Neural networks compute gradients through the chain rule, and"),
        (REGULAR, 12, 72, 640, "each layer passes activations forward before errors flow back."),
        (REGULAR, 12, 72, 620, "Weights change after gradients reach the input layer of a model"),
        (REGULAR, 12, 72, 600, "and the loss guides how far each update should move weights."),
        (BOLD, 12, 72, 580, "Dropout"),
        (REGULAR, 12, 130, 580, "randomly silences neurons, which improves generalization."),
    ],
    [
        (BOLD, 24, 72, 700, "Tie Breaking"),
        (REGULAR, 12, 72, 660, "alpha bravo charlie delta echo foxtrot golf hotel india juliet"),
        (REGULAR, 12, 72, 640, "kilo lima mike november oscar papa quebec romeo sierra tango"),
        (REGULAR, 12, 72, 620, "uniform victor whiskey xray yankee zulu amber coral ivory jade"),
        (KOREAN, 12, 72, 600, "역전파는 신경망 학습의 핵심이다"),
        (KOREAN, 12, 72, 580, "역전파를 이해하면 신경망이 보인다"),
    ],
]


def _show(run: Run) -> bytes:
    font, size, x, y, text = run
    if font == KOREAN:
        shown = "<" + text.encode("utf-16-be").hex() + ">"
    else:
        shown = f"({text})"
    return f"BT /{font} {size} Tf {x} {y} Td {shown} Tj ET\n".encode("ascii")


type PageSpec = tuple[bytes, bytes, list[Run]]  # 페이지 사전에 더할 항목, 그리기 명령, 글

YELLOW_RGB = (255, 255, 204)
_YELLOW_FILL = b"1 1 0.8 rg 0 0 612 792 re f 0 0 0 rg\n"


def _render_runs(title: str) -> list[Run]:
    return [
        (BOLD, 24, 72, 700, title),
        (REGULAR, 18, 72, 600, "Target"),
        (REGULAR, 18, 300, 200, "Corner"),
    ]


# CropBox 밖이라 화면에 보이지 않는 글. 토큰으로 뽑히면 안 된다.
_HIDDEN: Run = (REGULAR, 12, 10, 40, "Hidden")
_CROP = b" /CropBox [50 100 562 742]"
# 가로 글 사이에 세로로 세운 글(아래→위로 읽힘). 그래프의 세로 축 제목 같은 경우다.
_SIDEWAYS = b"BT /R 14 Tf 0 1 -1 0 540 250 Tm (Sideways Label) Tj ET\n"
# 직립 글 사이에 거꾸로(180도) 뒤집힌 글.
_FLIPPED = b"BT /R 14 Tf -1 0 0 -1 450 150 Tm (Flipped Note) Tj ET\n"

# 렌더링·좌표 정합 확인용: 흰 배경, 연노랑 배경, 90도 회전, MediaBox와 다른 CropBox,
# 회전+CropBox, 180도 회전, 270도 회전, 가로·세로 글 혼합, 0도·180도 글 혼합.
RENDER_PAGES: list[PageSpec] = [
    (b"", b"", _render_runs("White Page")),
    (b"", _YELLOW_FILL, _render_runs("Yellow Page")),
    (b" /Rotate 90", b"", _render_runs("Rotated Page")),
    (_CROP, b"", [*_render_runs("Cropped Page"), _HIDDEN]),
    (b" /Rotate 90" + _CROP, b"", [*_render_runs("Rotated Crop"), _HIDDEN]),
    (b" /Rotate 180", b"", _render_runs("Upside Down")),
    (b" /Rotate 270", b"", _render_runs("Rotated Left")),
    (b"", _SIDEWAYS, _render_runs("Mixed Page")),
    (b"", _FLIPPED, _render_runs("Flipped Mix")),
]


def _sample_pages() -> list[PageSpec]:
    return [(b"", b"", [*_frame(no), *runs]) for no, runs in enumerate(PAGES, start=1)]


# 두 장 모아찍기: A4 한 쪽에 슬라이드 두 장. 슬라이드마다 바닥글이 있어 한 쪽에 두 번 나온다.
# 바닥글은 큰 간격으로 나뉜 구절 두 개("Vibe coding", "Kwon, Bokyung")다.
# 1쪽 위 슬라이드는 표지라 같은 문구가 대소문자만 다르게 한 번 더 나오고,
# "AI coding tools"처럼 구절의 일부만 겹치는 제목이 본문에 있다.
def _slide_footer(y: int) -> list[Run]:
    return [(REGULAR, 9, 72, y, "Vibe coding"), (REGULAR, 9, 400, y, "Kwon, Bokyung")]


TWOUP_SLIDES: list[list[Run]] = [
    [
        (BOLD, 28, 72, 700, "Vibe Coding"),
        (REGULAR, 14, 72, 660, "LLM, and generative AI coding tools"),
        (REGULAR, 14, 72, 630, "Kwon, Bokyung"),
    ],
    [
        (BOLD, 20, 72, 350, "AI coding tools"),
        (REGULAR, 12, 72, 320, "An agent calls the model through an API key and returns"),
        (REGULAR, 12, 72, 300, "tokens that the editor inserts into the file."),
    ],
    [
        (BOLD, 20, 72, 730, "Prompting"),
        (REGULAR, 12, 72, 700, "A prompt gives the model context, examples and the task,"),
        (REGULAR, 12, 72, 680, "and the agent plans before it edits the file."),
    ],
    [
        (BOLD, 20, 72, 350, "Agents"),
        (REGULAR, 12, 72, 320, "An agent loops over tools until the task is done, and the"),
        (REGULAR, 12, 72, 300, "editor shows every edit for review."),
    ],
    [
        (BOLD, 20, 72, 730, "Context window"),
        (REGULAR, 12, 72, 700, "The model reads only the tokens inside its context window,"),
        (REGULAR, 12, 72, 680, "so the agent summarizes long files before coding."),
    ],
    [
        (BOLD, 20, 72, 350, "Evaluation"),
        (REGULAR, 12, 72, 320, "Tests and review catch errors that the model makes, and"),
        (REGULAR, 12, 72, 300, "the API key stays outside the repository."),
    ],
]


def _twoup_pages(slides: list[list[Run]]) -> list[PageSpec]:
    pairs = zip(slides[::2], slides[1::2], strict=True)
    return [
        (b"", b"", [*upper, *_slide_footer(420), *lower, *_slide_footer(30)])
        for upper, lower in pairs
    ]


# 문제·예시 슬라이드(#66): 1쪽 위 예시/아래 개념, 2쪽 위 개념/아래 연습문제, 3쪽 둘 다 문제,
# 4쪽 둘 다 개념인데 본문에 "for example"과 줄 첫머리의 "example,"이 있다(오탐 방지).
# 5쪽 둘 다 개념인데 위 슬라이드 본문 중간에 "Example:" 줄이 있다(그 문단만 빠져야 한다).
def _concept(y: int, title: str, first: str, second: str) -> list[Run]:
    # 개념 슬라이드 본문은 슬라이드마다 다르게 둔다. 같은 글이 같은 자리에 반복되면 바닥글로 잡힌다.
    return [
        (BOLD, 20, 72, y, title),
        (REGULAR, 12, 72, y - 30, first),
        (REGULAR, 12, 72, y - 50, second),
    ]


QUESTION_SLIDES: list[list[Run]] = [
    [
        (BOLD, 20, 72, 730, "Example: Interval Estimation"),
        (REGULAR, 12, 72, 700, "To estimate the average tensile strength of steel wires,"),
        (REGULAR, 12, 72, 680, "30 samples were tested. Find the 95% confidence interval."),
        (REGULAR, 12, 72, 640, "Solution: 62.5 plus or minus 1.343"),
    ],
    _concept(
        350,
        "Context window",
        "The model reads only the tokens inside its context window,",
        "so the agent summarizes long files before editing.",
    ),
    _concept(
        730,
        "Prompting",
        "A prompt gives the model context, examples and the task,",
        "and the agent plans before it edits the file.",
    ),
    [
        (BOLD, 20, 72, 350, "Exercise 1"),
        (REGULAR, 12, 72, 320, "Write a prompt that makes the agent plan before editing."),
        (REGULAR, 12, 72, 300, "Explain why the context window limits the plan."),
    ],
    [
        (BOLD, 20, 72, 730, "Q1. What does the agent do first?"),
        (REGULAR, 12, 72, 700, "Choose one: plan, edit, summarize."),
    ],
    [
        (KOREAN, 20, 72, 350, "문제 2"),
        (REGULAR, 12, 72, 320, "Describe the context window in one sentence."),
    ],
    [
        (BOLD, 20, 72, 730, "Agents"),
        (REGULAR, 12, 72, 700, "An agent loops over tools until the task is done, for example"),
        (REGULAR, 12, 72, 680, "example, the agent plans, edits the file and runs the tests."),
    ],
    _concept(
        350,
        "Evaluation",
        "Tests and review catch errors that the model makes, and",
        "the API key stays outside the repository.",
    ),
    [
        (BOLD, 20, 72, 730, "Gradient Descent"),
        (REGULAR, 12, 72, 700, "Gradient descent minimizes the loss function iteratively."),
        (REGULAR, 12, 72, 680, "Example: learning rate controls the step size"),
        (REGULAR, 12, 72, 640, "Momentum accelerates convergence on flat regions."),
    ],
    _concept(
        350,
        "Backpropagation",
        "Backpropagation applies the chain rule layer by layer,",
        "and the optimizer updates every weight with its gradient.",
    ),
]


def _objects(pages: list[PageSpec]) -> list[bytes]:
    font_ids = {name: 3 + index for index, (name, _) in enumerate(FONTS)}
    first_page_id = 3 + len(FONTS)
    page_ids = [first_page_id + 2 * index for index in range(len(pages))]
    kids = b" ".join(b"%d 0 R" % page_id for page_id in page_ids)
    fonts = b" ".join(b"/%s %d 0 R" % (name.encode(), id_) for name, id_ in font_ids.items())
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(pages)),
        *[body for _, body in FONTS],
    ]
    for page_id, (extra, drawing, runs) in zip(page_ids, pages, strict=True):
        stream = drawing + b"".join(_show(run) for run in runs)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]%s /Contents %d 0 R "
            b"/Resources << /Font << %s >> >> >>" % (extra, page_id + 1, fonts)
        )
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    return objects


def build_sample_pdf() -> bytes:
    return _build(_sample_pages())


def build_render_pdf() -> bytes:
    return _build(RENDER_PAGES)


def build_twoup_pdf() -> bytes:
    return _build(_twoup_pages(TWOUP_SLIDES))


def build_questions_pdf() -> bytes:
    return _build(_twoup_pages(QUESTION_SLIDES))


def _build(pages: list[PageSpec]) -> bytes:
    # 머리말의 NUL 바이트: git이 이 파일을 이진으로 보게 해 줄바꿈 변환(autocrlf)을 막는다.
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\x00\n")
    objects = _objects(pages)
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer << /Size %d /Root 1 0 R >>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


if __name__ == "__main__":
    Path(__file__).with_name("sample.pdf").write_bytes(build_sample_pdf())
    Path(__file__).with_name("render.pdf").write_bytes(build_render_pdf())
    Path(__file__).with_name("twoup.pdf").write_bytes(build_twoup_pdf())
    Path(__file__).with_name("questions.pdf").write_bytes(build_questions_pdf())
