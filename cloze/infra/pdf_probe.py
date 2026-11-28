"""업로드 PDF 파싱 검사. 제한(시간·메모리)을 건 하위 프로세스에서만 돈다(cloze/api/pdf_check.py).

실행: python -I -m cloze.infra.pdf_probe <메모리 MB>  (stdin으로 PDF, stdout으로 JSON)

표준 라이브러리와 pdfminer만 import한다. 검사마다 새 프로세스를 띄우므로 fastapi·pydantic을
불러오면 기동 시간(Linux 약 0.2초, Windows 약 0.5초)이 5초 한도에서 빠지고, 메모리 한도를
걸기 전에 가상 메모리도 그만큼 먼저 잡는다(#101 리뷰).
"""

import io
import json
import sys
from typing import Any


def limit_memory(memory_mb: int) -> None:
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


def inspect(pdf: bytes) -> dict[str, Any]:
    """결과는 셋 중 하나다: {"encrypted"|"restricted"|"javascript": True} 또는 {"pages": n}."""
    from pdfminer.pdfdocument import PDFDocument, PDFEncryptionError, PDFPasswordIncorrect
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser

    try:
        # 빈 암호로 연다(기본값). 열기(사용자) 암호가 걸렸으면 여기서 실패한다.
        document = PDFDocument(PDFParser(io.BytesIO(pdf)))
    except (PDFPasswordIncorrect, PDFEncryptionError):
        return {"encrypted": True}
    if document.encryption is not None:
        # 빈 암호로 열렸지만 권한(소유자) 암호가 걸린 파일. 받지 않는다(#101 리뷰 선택지 A).
        # 열기 암호와 사용자가 겪는 상황이 달라서 메시지를 나눈다.
        return {"restricted": True}
    if _has_javascript(document):
        return {"javascript": True}
    return {"pages": sum(1 for _ in PDFPage.create_pages(document))}


if __name__ == "__main__":
    limit_memory(int(sys.argv[1]))
    print(json.dumps(inspect(sys.stdin.buffer.read())))
