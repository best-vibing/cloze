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


def redacted_key(document_id: uuid.UUID, set_id: uuid.UUID, page_no: int) -> str:
    # 문서 폴더 아래에 두어, 문서를 지울 때 정리 작업이 한 폴더만 보면 되게 한다.
    return f"documents/{document_id}/blank_sets/{set_id}/{page_no}.webp"


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
        try:
            temp.write_bytes(data)
            os.replace(temp, path)
        except BaseException:
            # 디스크가 꽉 찼거나 대상이 열려 있으면(Windows) 여기로 온다. 임시 파일을 가리키는
            # 행이 없어 정리 작업도 놓치기 쉬우니 바로 지운다.
            temp.unlink(missing_ok=True)
            raise

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        """파일만 지운다. 없어도 오류가 아니다.

        빈 문서 폴더는 남긴다. 여기서 폴더를 지우면 같은 폴더에 쓰는 쪽의 mkdir과 write 사이에
        끼어들 수 있다. 빈 폴더 정리는 정리 작업(#30)이 맡는다.
        """
        self._path(key).unlink(missing_ok=True)
