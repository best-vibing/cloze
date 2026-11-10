// 프론트 스파이크(#57) — 순수 HTML/JS 후보.
// 페이지 결과 JSON(데이터 계약 1.4)과 가린 이미지만으로 입력칸을 겹친다.
// 입력칸 규칙: docs/design/grading-and-overlay.md 3.2

const FIXTURES = [{ json: "../fixtures/page-1.json", image: "../fixtures/page-1.png" }];
const MAX_GROW = 3; // 입력이 길어질 때 bbox 폭의 최대 몇 배까지 늘릴지

/**
 * 페이지 결과(계약 1.4)를 `GET /blank-sets/{id}`의 페이지 모양으로 바꾼다.
 * 정답(answer, variants)은 버린다. 화면에는 정답이 없어야 한다(D2).
 * order는 읽는 순서(줄 → x좌표)다.
 */
function toApiPage(result, imageUrl) {
  const lineOf = new Map(result.tokens.map((t) => [t.id, t.line_id]));
  const blanks = result.blanks
    .map((b, i) => ({
      blank_id: `p${result.page.page_no}-b${i}`,
      line: lineOf.get(b.token_ids[0]),
      bbox: b.bbox,
      type: b.type,
      hint: b.hint,
      style: b.style,
    }))
    .sort((a, b) => a.line - b.line || a.bbox[0] - b.bbox[0])
    .map(({ line, ...b }, i) => ({ ...b, order: i + 1 }));
  return { ...result.page, image_url: imageUrl, blanks };
}


/** 입력한 글자 폭에 맞춰 입력칸을 늘린다. 단위는 cqw라 창 크기에 따라 같이 변한다. */
function fitWidth(input, measure) {
  const [x0, , x1] = input.blank.bbox;
  const minCqw = (x1 - x0) * 100;
  const maxCqw = Math.min(minCqw * MAX_GROW, (1 - x0) * 100);
  const pageWidth = input.parentElement.clientWidth;
  measure.font = getComputedStyle(input).font;
  const textCqw = ((measure.measureText(input.value).width + 8) / pageWidth) * 100;
  input.style.width = `${Math.min(Math.max(minCqw, textCqw), maxCqw)}cqw`;
}

function makeInput(page, blank, measure) {
  const [x0, y0, x1, y1] = blank.bbox;
  const input = document.createElement("input");
  input.className = "blank";
  input.type = "text";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.dataset.order = blank.order;
  input.setAttribute("aria-label", `페이지 ${page.page_no}, 빈칸 ${blank.order}`);
  input.blank = blank;
  Object.assign(input.style, {
    left: `${x0 * 100}%`,
    top: `${y0 * 100}%`,
    width: `${(x1 - x0) * 100}cqw`, // 정답 길이가 아니라 bbox 폭에서 시작한다
    height: `${(y1 - y0) * 100}%`,
  });
  input.style.setProperty("--font-ratio", blank.style.font_size_ratio);
  input.style.setProperty("--font-color", blank.style.font_color);
  input.style.setProperty("--bg-color", blank.style.bg_color);
  input.addEventListener("input", () => fitWidth(input, measure));
  return input;
}

function renderPage(page, measure) {
  const box = document.createElement("section");
  box.className = "page";
  box.setAttribute("aria-label", `페이지 ${page.page_no}`);
  const img = document.createElement("img");
  img.src = page.image_url;
  img.alt = `페이지 ${page.page_no}(빈칸 자리를 가린 이미지)`;
  img.width = page.width_pt;
  img.height = page.height_pt;
  box.append(img);
  // DOM 순서를 order 순서로 넣어 Tab 이동이 읽는 순서를 따르게 한다.
  for (const blank of page.blanks) box.append(makeInput(page, blank, measure));
  return box;
}

/** Enter는 다음 빈칸, Shift+Enter는 이전 빈칸. 한글 조합 중에는 Enter·Tab 이동을 막는다. */
function onKeydown(event) {
  const input = event.target;
  if (!input.classList?.contains("blank")) return;
  const composing = event.isComposing || event.keyCode === 229;
  if (composing && (event.key === "Enter" || event.key === "Tab")) {
    event.preventDefault();
    return;
  }
  if (event.key !== "Enter") return;
  event.preventDefault();
  const all = [...document.querySelectorAll(".blank")];
  const next = all[all.indexOf(input) + (event.shiftKey ? -1 : 1)];
  next?.focus();
}

function showHint(event, hintEl) {
  const blank = event.target.blank;
  if (!blank) return;
  // 서버가 준 글자는 textContent로만 넣는다(HTML 해석 금지).
  hintEl.textContent = blank.hint ? `힌트: ${blank.hint}` : "";
}

async function main() {
  const pagesEl = document.getElementById("pages");
  const hintEl = document.getElementById("hint");
  const measure = document.createElement("canvas").getContext("2d");
  for (const f of FIXTURES) {
    const result = await (await fetch(f.json)).json();
    pagesEl.append(renderPage(toApiPage(result, f.image), measure));
  }
  document.addEventListener("keydown", onKeydown);
  document.addEventListener("focusin", (e) => showHint(e, hintEl));
  document.getElementById("debug").addEventListener("change", (e) => {
    document.body.classList.toggle("debug", e.target.checked);
  });
}

main();
