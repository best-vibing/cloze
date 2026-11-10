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

// ---- 가짜 채점 서버 ------------------------------------------------------------
// `POST /blank-sets/{id}/attempts`를 흉내 낸다. 정답은 이 안에만 있고 화면 쪽 데이터로 나가지 않는다.
// 판정은 grading-and-overlay.md 1.1의 일부(정규화·조사·숫자·near)만 옮긴 시험용이다. 실제 채점은 #55 core/grade.

const JOSA = ["에서", "으로", "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도", "만"];

function normalize(text) {
  return text
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[\p{P}\p{S}]/gu, "")
    .replace(/\s+/g, " ")
    .trim();
}

function stripJosa(text) {
  const josa = JOSA.find((j) => text.length > j.length && text.endsWith(j));
  return josa ? text.slice(0, -josa.length) : text;
}

function editDistance(a, b) {
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = cur;
  }
  return prev[b.length];
}

function judge(input, blank) {
  const given = normalize(input);
  const accepted = [blank.answer, ...blank.variants].map(normalize);
  if (accepted.includes(given) || accepted.includes(stripJosa(given))) return "correct";
  if (blank.type === "number" && given !== "" && Number(given) === Number(accepted[0])) {
    return "correct";
  }
  if (accepted[0].length >= 5 && editDistance(given, accepted[0]) <= 1) return "near";
  return "wrong";
}

function createFakeServer(pageResults) {
  const answers = new Map();
  for (const r of pageResults) {
    r.blanks.forEach((b, i) => answers.set(`p${r.page.page_no}-b${i}`, b));
  }
  let attemptNo = 0;
  return {
    async submitAttempt(given) {
      const results = {};
      for (const [id, blank] of answers) results[id] = judge(given[id] ?? "", blank);
      const correct = Object.values(results).filter((v) => v === "correct").length;
      attemptNo += 1;
      return { attempt_id: `fake-${attemptNo}`, results, score: correct / answers.size };
    },
  };
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

/**
 * Enter는 다음 빈칸, Shift+Enter는 이전 빈칸. 마지막 빈칸에서 Enter를 누르면 정답을 확인한다.
 * 한글 조합 중에는 Enter·Tab 이동을 막는다.
 */
function onKeydown(event, submit) {
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
  const index = all.indexOf(input);
  if (!event.shiftKey && index === all.length - 1) {
    submit();
    return;
  }
  all[index + (event.shiftKey ? -1 : 1)]?.focus();
}

/** 입력값을 모아 채점을 요청하고, 칸마다 결과를 표시한다. */
async function submitAnswers(server, statusEl) {
  const inputs = [...document.querySelectorAll(".blank")];
  const answers = Object.fromEntries(inputs.map((i) => [i.blank.blank_id, i.value]));
  const { results, score } = await server.submitAttempt(answers);
  const count = { correct: 0, near: 0, wrong: 0 };
  for (const input of inputs) {
    const result = results[input.blank.blank_id];
    count[result] += 1;
    input.dataset.result = result;
    input.setAttribute("aria-invalid", String(result !== "correct"));
  }
  const near = count.near ? `, 거의 맞음 ${count.near}칸` : "";
  statusEl.textContent =
    `${inputs.length}칸 중 ${count.correct}칸 정답(${Math.round(score * 100)}점)${near}. ` +
    "고친 칸은 표시가 지워지고, 다시 확인할 수 있어요.";
}

/** 결과를 받은 칸을 고치면 그 칸의 표시를 지운다. */
function clearResult(event) {
  const input = event.target;
  if (!input.dataset?.result) return;
  delete input.dataset.result;
  input.removeAttribute("aria-invalid");
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
  const resultEl = document.getElementById("result");
  const measure = document.createElement("canvas").getContext("2d");
  const pageResults = [];
  for (const f of FIXTURES) {
    const result = await (await fetch(f.json)).json();
    pageResults.push(result);
    pagesEl.append(renderPage(toApiPage(result, f.image), measure));
  }
  const server = createFakeServer(pageResults);
  const submit = () => submitAnswers(server, resultEl);
  document.getElementById("check").addEventListener("click", submit);
  document.addEventListener("keydown", (e) => onKeydown(e, submit));
  document.addEventListener("input", clearResult);
  document.addEventListener("focusin", (e) => showHint(e, hintEl));
  document.getElementById("debug").addEventListener("change", (e) => {
    document.body.classList.toggle("debug", e.target.checked);
  });
}

main();
