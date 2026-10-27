# ADR-002: PDF 라이브러리 — pypdfium2 + pdfplumber 채택

## 상태
채택됨 (2026-10-27)

## 컨텍스트

PDF에서 필요한 기능은 세 가지다.

1. 페이지를 이미지로 렌더링
2. 단어·글자 단위 좌표 추출
3. 글자의 폰트 크기·색 추출(입력칸을 원문에 녹아 보이게 만들 때)

서비스는 비공개로 배포할 예정이다. 그래서 네트워크로 서비스만 제공해도 소스 공개 의무가 생기는 라이선스는 피해야 한다.

## 검토한 선택지

| 선택지 | 장점 | 단점 | 결론 |
|---|---|---|---|
| PyMuPDF | 렌더링, 좌표, 폰트, PDF 리댁션을 한 라이브러리로 해결 | AGPL-3.0 또는 Artifex 상용 라이선스 [확인]. 비공개 운영이면 상용 구매가 필요 | 탈락 |
| **pypdfium2 + pdfplumber** | 라이선스 부담 없음(pypdfium2: Apache-2.0 / BSD-3-Clause, pdfplumber: MIT) [확인] | 라이브러리가 둘. PDF 리댁션 기능이 없음 | **채택** |
| pdf2image(Poppler) | 렌더링이 쉬움 | 좌표 추출이 없고 GPL 계열로 알려짐 [미확인] | 단독으로는 부족 → 탈락 |
| 브라우저에서 pdf.js | 업로드가 필요 없음 | 정답이 클라이언트에 있어야 해서 정답 보호(서버 채점)가 불가능 | 탈락 |

## 결정

렌더링은 pypdfium2, 좌표와 폰트 정보는 pdfplumber로 한다.

- pdfplumber 좌표는 포인트 단위이고, `top`·`bottom`은 페이지 위쪽 기준이다 [확인]. 이 두 필드를 쓰면 y축을 뒤집을 필요가 없다.
- 글자 단위 정보에 `fontname`, `size`, `non_stroking_color`(글자 내부 색), `stroking_color`(외곽선 색)가 있다 [확인].

## 결과

- PDF 안의 글자를 지우는 리댁션을 쓸 수 없다. 그래서 리댁션을 래스터 채우기로 정했다(ADR-003).
- 두 라이브러리가 그린 이미지와 추출한 좌표가 같은 페이지 기준인지(회전된 페이지 포함) 로드맵 0단계 박스 뷰어로 눈으로 확인한다.
- PDF 백엔드는 어댑터 뒤에 둔다. 라이선스 조건이 바뀌면 PyMuPDF로 교체할 수 있다.
- 처리 속도는 비교해 보지 않았다 [미확인]. 30쪽 실측 때 함께 잰다.

## 출처

- PyMuPDF 라이선스: https://pypi.org/project/PyMuPDF/1.19.0/
- pypdfium2 라이선스: https://pypi.org/project/pypdfium2/
- pdfplumber 좌표·글자 필드: https://github.com/jsvine/pdfplumber/blob/stable/README.md
- pdfplumber 라이선스: https://github.com/jsvine/pdfplumber/blob/stable/LICENSE.txt
