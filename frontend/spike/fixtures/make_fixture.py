"""프론트 스파이크(#57)용 가짜 페이지 이미지와 페이지 결과 JSON(계약 1.4)을 만든다.

서버도, 아직 머지되지 않은 `mask` 명령도 없이 입력칸 겹치기를 시험하려고 쓴다.
글자를 직접 그리므로 토큰 bbox가 이미지와 정확히 맞는다. 빈칸 자리는 배경색으로 칠한다.

    uv run python frontend/spike/fixtures/make_fixture.py [한글 글꼴 경로]
"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH_PT, HEIGHT_PT = 720, 540
SCALE = 2  # 1pt = 2px
BG = "#ffffff"
OUT_DIR = Path(__file__).parent

# (글자 크기 pt, 글자색, 줄 내용). 줄 내용의 [대괄호] 단어가 빈칸이 된다.
LINES = [
    (28, "#1f3a93", "세포의 구조와 기능"),
    (16, "#1a1a1a", "• [미토콘드리아]는 세포 호흡으로 [ATP]를 만든다"),
    (16, "#1a1a1a", "• 단백질 합성은 [리보솜]에서 일어난다"),
    (16, "#1a1a1a", "• [Golgi] apparatus packages proteins for secretion"),
    (16, "#1a1a1a", "• 세포막은 [인지질] 이중층으로 이루어진다"),
    (16, "#c0392b", "• 로버트 훅은 [1665]년에 세포를 처음 관찰했다"),
]
HINTS = {"미토콘드리아": "세포 소기관", "Golgi": "소기관 이름(영어)"}


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def _norm(box: tuple[float, float, float, float]) -> list[float]:
    """픽셀 bbox를 페이지 기준 0~1 좌표로 바꾼다."""
    w, h = WIDTH_PT * SCALE, HEIGHT_PT * SCALE
    return [round(box[0] / w, 4), round(box[1] / h, 4), round(box[2] / w, 4), round(box[3] / h, 4)]


def build(font_path: str) -> None:
    """이미지와 JSON을 만들어 이 폴더에 저장한다."""
    img = Image.new("RGB", (WIDTH_PT * SCALE, HEIGHT_PT * SCALE), BG)
    draw = ImageDraw.Draw(img)
    tokens: list[dict[str, object]] = []
    blanks: list[dict[str, object]] = []
    y = 50 * SCALE
    for line_id, (size_pt, color, text) in enumerate(LINES):
        font = ImageFont.truetype(font_path, size_pt * SCALE)
        x = 50 * SCALE
        space = draw.textlength(" ", font=font)
        for word in text.split(" "):
            is_blank = word.startswith("[")
            core = word.strip("[]").split("]")[0]
            rest = word.split("]")[1] if "]" in word else ""
            box = draw.textbbox((x, y), core, font=font)
            draw.text((x, y), core, font=font, fill=color)
            token_id = len(tokens)
            tokens.append(
                {"id": token_id, "text": core, "bbox": _norm(box), "line_id": line_id,
                 "block_id": 0, "font_size": float(size_pt), "font_color": color, "conf": 1.0}
            )  # fmt: skip
            if is_blank:
                pad = 2 * SCALE
                padded = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
                draw.rectangle(padded, fill=BG)
                blanks.append(
                    {"token_ids": [token_id], "answer": core, "bbox": _norm(padded),
                     "type": "number" if core.isdigit() else "term", "hint": HINTS.get(core),
                     "variants": [], "answer_group": None,
                     "style": {"font_size_ratio": round(size_pt / WIDTH_PT, 4),
                               "font_color": color, "bg_color": BG}}
                )  # fmt: skip
            x += draw.textlength(core, font=font)
            if rest:
                draw.text((x, y), rest, font=font, fill=color)
                x += draw.textlength(rest, font=font)
            x += space
        y += int(size_pt * SCALE * 2.2)

    result = {
        "schema_version": 0,
        "page": {"page_no": 1, "width_pt": WIDTH_PT, "height_pt": HEIGHT_PT, "source": "textlayer"},
        "tokens": tokens,
        "blanks": blanks,
        "selection": {"source": "fallback", "attempts": 1, "quality": {"survival_ratio": 1.0}},
    }
    img.save(OUT_DIR / "page-1.png", optimize=True)
    (OUT_DIR / "page-1.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "C:/Windows/Fonts/malgun.ttf")
