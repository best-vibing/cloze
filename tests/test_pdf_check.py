from pathlib import Path

import pytest

from cloze.api.errors import ApiError
from cloze.api.pdf_check import check_pdf
from cloze.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = Settings(upload_max_bytes=100_000, upload_max_pages=3)


def make_pdf(
    pages: int = 1, catalog_extra: str = "", trailer_extra: str = "", extra: tuple[str, ...] = ()
) -> bytes:
    """xref 오프셋까지 맞는 작은 PDF. 추가 객체 번호는 3 + pages부터다."""
    kids = " ".join(f"{3 + i} 0 R" for i in range(pages))
    objects = [
        f"<< /Type /Catalog /Pages 2 0 R {catalog_extra} >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>",
        *["<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>"] * pages,
        *extra,
    ]
    out = b"%PDF-1.7\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    trailer = f"<< /Size {len(objects) + 1} /Root 1 0 R {trailer_extra} >>"
    return out + f"trailer\n{trailer}\nstartxref\n{xref}\n%%EOF\n".encode()


def _code(pdf: bytes, settings: Settings = SETTINGS) -> str:
    with pytest.raises(ApiError) as caught:
        check_pdf(pdf, settings)
    return caught.value.code


def test_accepts_plain_pdf_and_counts_pages() -> None:
    assert check_pdf(make_pdf(pages=2), SETTINGS) == 2
    real = (FIXTURES / "sample.pdf").read_bytes()
    assert check_pdf(real, Settings()) >= 1


def test_size_and_signature() -> None:
    assert _code(b"%PDF-" + b"0" * 100_000) == "file_too_large"
    assert _code(b"PK\x03\x04 zip file") == "not_pdf"
    assert _code(b"%PDF-1.7\n garbage only") == "not_pdf"  # 시그니처만 있고 읽을 수 없음


def test_too_many_pages() -> None:
    assert _code(make_pdf(pages=4)) == "too_many_pages"


def test_rejects_javascript() -> None:
    open_action = make_pdf(
        catalog_extra="/OpenAction 4 0 R", extra=("<< /S /JavaScript /JS (app.alert(1)) >>",)
    )
    assert _code(open_action) == "encrypted_pdf"
    names_tree = make_pdf(
        catalog_extra="/Names << /JavaScript 4 0 R >>", extra=("<< /Names [] >>",)
    )
    assert _code(names_tree) == "encrypted_pdf"


def test_rejects_encrypted() -> None:
    encrypt = "<< /Filter /Standard /V 1 /R 2 /O <{0}> /U <{0}> /P -4 >>".format("ab" * 32)
    pdf = make_pdf(trailer_extra="/Encrypt 4 0 R /ID [<00> <00>]", extra=(encrypt,))
    assert _code(pdf) == "encrypted_pdf"


def test_probe_timeout_is_not_pdf() -> None:
    slow = Settings(upload_probe_timeout_s=0.001)
    assert _code(make_pdf(), slow) == "not_pdf"
