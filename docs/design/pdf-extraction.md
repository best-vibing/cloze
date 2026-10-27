# 상세 설계서 — PDF 처리·좌표 추출

- 상위 문서: [아키텍처 정의서](../architecture.md) 4장
- 관련 결정: [ADR-002 PDF 라이브러리](../decisions/ADR-002-pdf-library-pdfplumber.md), [ADR-003 래스터 리댁션](../decisions/ADR-003-raster-redaction.md)

## 1. 라이브러리 역할

| 라이브러리 | 용도 |
|---|---|
| pypdfium2 | 페이지 → 이미지 렌더링 |
| pdfplumber (pdfminer.six 기반) | 단어·글자 좌표, 폰트 정보 |

## 2. 좌표 규약

- pdfplumber 좌표는 **포인트 단위이고 `top`·`bottom`이 페이지 위쪽 기준**이다 [확인]. DESIGN.md 7.1의 "원점이 좌하단일 수 있다 [미확인]"은 `top`·`bottom` 필드를 쓰면 y축 뒤집기가 필요 없다는 것으로 정리된다.
- 저장 좌표는 DESIGN.md C4대로 페이지 기준 0~1, 좌상단 원점.
- 폰트 크기·색은 pdfplumber의 글자 단위 정보(`fontname`, `size`, `non_stroking_color`)에서 가져온다 [확인]. 색 값의 형식(색 공간별 표현)은 pdfplumber `docs/colors.md`를 구현 시 참고.

## 3. OCR 경로(스캔본)

로드맵 7단계. PaddleOCR 1순위 검토는 그대로. OCR 도구의 라이선스는 7단계 전에 확인한다 [미확인].

## 출처

- pdfplumber 좌표 규약·글자 필드: https://github.com/jsvine/pdfplumber/blob/stable/README.md
