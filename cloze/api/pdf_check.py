"""업로드 검사(데이터 계약 3절, 신뢰성 설계 2·5절).

크기·시그니처는 요청 안에서 본다. 쪽수·암호화·JavaScript는 PDF를 파싱해야 하므로
제한(시간·메모리)을 건 하위 프로세스에서 본다. 악성·손상 PDF가 파서를 멈추거나 메모리를
다 써도 API 프로세스는 살아남는다(아키텍처: 파서 격리).

하위 프로세스 진입점: python -I -m cloze.api.pdf_check <메모리 MB>  (stdin으로 PDF, stdout으로 JSON)
"""

import io
import json

# 고정된 인자로 자기 자신만 실행한다(셸 없음, 사용자 입력은 표준 입력으로만).
import subprocess  # nosec B404
import sys
from typing import Any

from cloze.api.errors import ApiError
from cloze.config import Settings


def check_pdf(pdf: bytes, settings: Settings) -> int:
    """검사를 통과하면 쪽수를 돌려준다. 걸리면 계약 3절의 ApiError."""
    if len(pdf) > settings.upload_max_bytes:
        raise ApiError("file_too_large", "파일이 너무 커요.")
    # PDF 표준은 %PDF- 앞에 쓰레기 바이트를 허용한다. 실제 리더들처럼 앞 1KB 안에서 찾는다.
    if b"%PDF-" not in pdf[:1024]:
        raise ApiError("not_pdf", "PDF 파일만 올릴 수 있어요.")
    found = _probe(pdf, settings.upload_probe_timeout_s, settings.upload_probe_memory_mb)
    if found.get("encrypted") or found.get("javascript"):
        raise ApiError("encrypted_pdf", "암호가 걸렸거나 스크립트가 든 PDF는 올릴 수 없어요.")
    pages = found.get("pages")
    if not isinstance(pages, int) or pages < 1:
        raise ApiError("not_pdf", "PDF를 읽을 수 없어요.")
    if pages > settings.upload_max_pages:
        raise ApiError("too_many_pages", f"{settings.upload_max_pages}쪽까지 올릴 수 있어요.")
    return pages


def _probe(pdf: bytes, timeout_s: float, memory_mb: int) -> dict[str, Any]:
    command = [sys.executable, "-I", "-m", "cloze.api.pdf_check", str(memory_mb)]
    try:
        done = subprocess.run(  # nosec B603
            command, input=pdf, capture_output=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired:
        raise ApiError("not_pdf", "PDF를 읽는 데 너무 오래 걸려요.") from None
    if done.returncode != 0:
        # 파싱 실패, 메모리 한도 초과 등. 손상된 PDF로 본다.
        raise ApiError("not_pdf", "PDF를 읽을 수 없어요.")
    result: dict[str, Any] = json.loads(done.stdout)
    return result


# --- 아래는 하위 프로세스에서만 돈다 ---


def _limit_memory(memory_mb: int) -> None:
    # ponytail: Windows는 메모리 한도 없음(시간 한도만). 운영은 Linux 컨테이너라 걸린다.
    if sys.platform != "win32":
        import resource

        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _contains_javascript(value: Any, depth: int = 0) -> bool:
    """사전·배열 안까지 내려가며 JavaScript 동작(/S /JavaScript, /JS)이나 이름 트리를 찾는다.

    참조(R)는 따라가지 않는다. 참조된 객체는 _has_javascript가 따로 훑는다.
    """
    if depth > 32:  # 비정상적으로 깊은 중첩
        return True
    if isinstance(value, dict):
        action = getattr(value.get("S"), "name", None)
        if "JS" in value or "JavaScript" in value or action == "JavaScript":
            return True
        return any(_contains_javascript(item, depth + 1) for item in value.values())
    if isinstance(value, list):
        return any(_contains_javascript(item, depth + 1) for item in value)
    return False


def _has_javascript(document: Any) -> bool:
    """모든 객체를 훑는다. 압축된 객체 스트림 안의 객체도 getobj가 풀어서 돌려준다."""
    for xref in document.xrefs:
        for objid in xref.get_objids():
            try:
                obj = document.getobj(objid)
            # 깨진 객체 하나로 검사를 멈추지 않는다.
            except Exception:  # noqa: BLE001  # nosec B112
                continue
            if _contains_javascript(obj):
                return True
    return False


def _inspect(pdf: bytes) -> dict[str, Any]:
    from pdfminer.pdfdocument import PDFDocument, PDFEncryptionError, PDFPasswordIncorrect
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser

    try:
        # 빈 암호로 열린다(기본값). 사용자 암호가 걸렸으면 여기서 실패한다.
        document = PDFDocument(PDFParser(io.BytesIO(pdf)))
    except (PDFPasswordIncorrect, PDFEncryptionError):
        return {"encrypted": True}
    if document.encryption is not None:
        return {"encrypted": True}
    if _has_javascript(document):
        return {"javascript": True}
    return {"pages": sum(1 for _ in PDFPage.create_pages(document))}


if __name__ == "__main__":
    _limit_memory(int(sys.argv[1]))
    print(json.dumps(_inspect(sys.stdin.buffer.read())))
