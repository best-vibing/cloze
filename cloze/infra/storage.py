"""파일 저장소(로컬 디스크). 업로드·워커·정리 작업이 같이 쓴다(#28).

키는 `documents/{문서 ID}/source.pdf`처럼 코드가 만든다. 사용자가 붙인 파일 이름은 키에 쓰지 않는다.
기능을 바꿀 때는 리뷰를 받는다(#28 3번). "있는지 확인"·"목록"은 정리 작업(#30)에서 더한다.
"""

import os
import secrets
import uuid
from pathlib import Path


def source_key(document_id: uuid.UUID) -> str:
    # 해시를 이름으로 쓰지 않는다: 지웠다 다시 올린 같은 PDF가 옛 파일과 이름이 겹친다(#28 2번).
    return f"documents/{document_id}/source.pdf"


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if path == self.root or not path.is_relative_to(self.root):
            raise ValueError(f"저장소 밖을 가리키는 키: {key!r}")
        return path

    def put(self, key: str, data: bytes) -> None:
        """임시 파일에 다 쓴 뒤 바꿔 끼운다. 쓰다 죽어도 반쪽 파일이 키 자리에 남지 않는다."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        temp.write_bytes(data)
        os.replace(temp, path)

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        """없어도 오류가 아니다. 비게 된 문서 폴더도 지운다."""
        path = self._path(key)
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass  # 다른 파일이 남아 있거나 이미 없음
