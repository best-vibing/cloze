"""tests/fixtures/sample.pdf를 표준 라이브러리만으로 만든다.

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


def _objects() -> list[bytes]:
    font_ids = {name: 3 + index for index, (name, _) in enumerate(FONTS)}
    first_page_id = 3 + len(FONTS)
    page_ids = [first_page_id + 2 * index for index in range(len(PAGES))]
    kids = b" ".join(b"%d 0 R" % page_id for page_id in page_ids)
    fonts = b" ".join(b"/%s %d 0 R" % (name.encode(), id_) for name, id_ in font_ids.items())
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(PAGES)),
        *[body for _, body in FONTS],
    ]
    for page_no, (page_id, runs) in enumerate(zip(page_ids, PAGES, strict=True), start=1):
        stream = b"".join(_show(run) for run in [*_frame(page_no), *runs])
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
            b"/Resources << /Font << %s >> >> >>" % (page_id + 1, fonts)
        )
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    return objects


def build_sample_pdf() -> bytes:
    # 머리말의 NUL 바이트: git이 이 파일을 이진으로 보게 해 줄바꿈 변환(autocrlf)을 막는다.
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\x00\n")
    objects = _objects()
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
