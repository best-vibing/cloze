import asyncio
import hashlib
import logging
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from cloze.api.errors import ApiError
from cloze.api.pdf_check import check_pdf
from cloze.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = Settings(upload_max_bytes=100_000, upload_max_pages=3)

# PDF 표준 보안의 암호 채움 문자열(ISO 32000-1 7.6.3.3)
PAD = bytes.fromhex("28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a")


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


def _rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray()
    for byte in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


def restricted_pdf(permissions: int) -> bytes:
    """열기 암호는 비어 있고 권한(소유자) 암호만 걸린 PDF(표준 보안 R2, 40비트 RC4)."""
    owner = b"\x11" * 32  # 소유자 암호 값은 검사에 쓰이지 않는다
    doc_id = b"\x00"
    # 빈 열기 암호로 만든 키로 채움 문자열을 암호화한 값이 /U다(알고리즘 2·4).
    seed = PAD + owner + struct.pack("<i", permissions) + doc_id
    key = hashlib.md5(seed, usedforsecurity=False).digest()[:5]
    user = _rc4(key, PAD)
    encrypt = f"<< /Filter /Standard /V 1 /R 2 /O <{owner.hex()}> /U <{user.hex()}> "
    encrypt += f"/P {permissions} >>"
    return make_pdf(trailer_extra=f"/Encrypt 4 0 R /ID [<{doc_id.hex()}> <00>]", extra=(encrypt,))


def _check(pdf: bytes, settings: Settings = SETTINGS) -> int:
    return asyncio.run(check_pdf(pdf, settings))


def _error(pdf: bytes, settings: Settings = SETTINGS) -> ApiError:
    with pytest.raises(ApiError) as caught:
        _check(pdf, settings)
    return caught.value


def test_accepts_plain_pdf_and_counts_pages() -> None:
    assert _check(make_pdf(pages=2)) == 2
    real = (FIXTURES / "sample.pdf").read_bytes()
    assert _check(real, Settings()) >= 1


def test_size_and_signature() -> None:
    assert _error(b"%PDF-" + b"0" * 100_000).code == "file_too_large"
    assert _error(b"PK\x03\x04 zip file").code == "not_pdf"
    assert _error(b"%PDF-1.7\n garbage only").code == "not_pdf"  # 시그니처만 있고 읽을 수 없음


def test_too_many_pages() -> None:
    assert _error(make_pdf(pages=4)).code == "too_many_pages"


def test_rejects_javascript() -> None:
    open_action = make_pdf(
        catalog_extra="/OpenAction 4 0 R", extra=("<< /S /JavaScript /JS (app.alert(1)) >>",)
    )
    names_tree = make_pdf(
        catalog_extra="/Names << /JavaScript 4 0 R >>", extra=("<< /Names [] >>",)
    )
    for pdf in (open_action, names_tree):
        error = _error(pdf)
        assert error.code == "encrypted_pdf" and "스크립트" in error.message


def test_rejects_open_password() -> None:
    # /U가 빈 암호로 만든 값과 다르다 = 열기 암호가 걸렸다.
    encrypt = "<< /Filter /Standard /V 1 /R 2 /O <{0}> /U <{0}> /P -4 >>".format("ab" * 32)
    pdf = make_pdf(trailer_extra="/Encrypt 4 0 R /ID [<00> <00>]", extra=(encrypt,))
    error = _error(pdf)
    assert error.code == "encrypted_pdf" and "암호가 걸린" in error.message


def test_rejects_permission_only_pdf_with_its_own_message() -> None:
    # 복사(내용 추출, 5번째 비트) 금지. 암호 없이 열리는 파일이라 "암호가 걸렸다"고 하지 않는다.
    error = _error(restricted_pdf(-4 & ~(1 << 4)))
    assert error.code == "encrypted_pdf" and "제한된" in error.message


def test_probe_timeout_is_not_pdf() -> None:
    slow = Settings(upload_probe_timeout_s=0.001)
    assert _error(make_pdf(), slow).code == "not_pdf"


def test_probe_crash_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # 배포 이미지에서 패키지를 못 찾거나 메모리 한도에 걸려도 원인이 로그에 남아야 한다.
    def crash(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 1, b"", b"Traceback\nModuleNotFoundError: x\n")

    monkeypatch.setattr(subprocess, "run", crash)
    with caplog.at_level(logging.WARNING, logger="cloze.api.pdf_check"):
        assert _error(make_pdf()).code == "not_pdf"
    assert "ModuleNotFoundError" in caplog.text


def test_probe_garbage_output_is_not_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    def garbage(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 0, b"not json", b"")

    monkeypatch.setattr(subprocess, "run", garbage)
    assert _error(make_pdf()).code == "not_pdf"  # 500이 아니다


def test_probe_module_does_not_import_web_stack() -> None:
    # 검사마다 새 프로세스를 띄우므로 fastapi·pydantic을 불러오면 기동 시간이 한도에서 빠진다.
    code = (
        "import sys, cloze.infra.pdf_probe; "
        "print(sorted({'fastapi', 'pydantic'} & set(sys.modules)))"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, check=True)
    assert done.stdout.strip() == b"[]"
