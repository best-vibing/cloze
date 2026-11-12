// 가짜 데이터 읽기와 가짜 채점 서버. 화면과 상관없는 코드라 순수 HTML/JS 후보(vanilla/app.js)와 같다.
// 두 후보의 차이는 화면 코드(App.svelte, Page.svelte)에서 비교한다.

export const FIXTURES = [
  { json: "mask/page_001.json", image: "mask/page_001.webp" },
  { json: "mask/page_002.json", image: "mask/page_002.webp" },
  { json: "mask/page_003.json", image: "mask/page_003.webp" },
  { json: "page-1.json", image: "page-1.png" },
];
const FONT_FROM_BBOX = 0.85; // [가설] style이 없을 때 글자 크기 = bbox 높이 × 0.85
const DEFAULT_FONT_COLOR = "#1a1a1a";

function fromContract(result) {
  const lineOf = new Map(result.tokens.map((t) => [t.id, t.line_id]));
  return result.blanks
    .map((b) => ({ ...b, line: lineOf.get(b.token_ids[0]) }))
    .sort((a, b) => a.line - b.line || a.bbox[0] - b.bbox[0])
    .map(({ line, ...b }, i) => ({ ...b, order: i + 1 }));
}

function fromMask(result) {
  const { width_pt: w, height_pt: h } = result.page;
  return result.blanks.map((b) => ({
    order: b.order,
    answer: b.answer,
    variants: [],
    bbox: b.bbox,
    type: "term",
    hint: null,
    style: {
      font_size_ratio: (b.bbox[3] - b.bbox[1]) * FONT_FROM_BBOX * (h / w),
      font_color: DEFAULT_FONT_COLOR,
      bg_color: b.bg_color,
    },
  }));
}

export function readFixture(result, pageNo) {
  const blanks = (result.tokens ? fromContract(result) : fromMask(result))
    .sort((a, b) => a.order - b.order)
    .map((b) => ({ ...b, blank_id: `p${pageNo}-o${b.order}` }));
  return { page: { ...result.page, page_no: pageNo }, blanks };
}

/** 정답(answer, variants)을 버리고 `GET /blank-sets/{id}`의 페이지 모양으로 바꾼다(D2). */
export function toApiPage({ page, blanks }, imageUrl) {
  const shown = blanks.map(({ blank_id, order, bbox, type, hint, style }) => ({
    blank_id, order, bbox, type, hint, style,
  }));
  return { ...page, image_url: imageUrl, blanks: shown };
}

// ---- 가짜 채점 서버 (실제 채점은 #55 core/grade) ----
const JOSA = ["에서", "으로", "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도", "만"];

function normalize(text) {
  return text.normalize("NFKC").toLowerCase().replace(/[\p{P}\p{S}]/gu, "").replace(/\s+/g, " ").trim();
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
  if (given && (accepted.includes(given) || accepted.includes(stripJosa(given)))) return "correct";
  if (blank.type === "number" && given !== "" && Number(given) === Number(accepted[0])) return "correct";
  if (given && accepted[0].length >= 5 && editDistance(given, accepted[0]) <= 1) return "near";
  return "wrong";
}

export function createFakeServer(pages) {
  const answers = new Map();
  for (const p of pages) for (const b of p.blanks) answers.set(b.blank_id, b);
  let attemptNo = 0;
  return {
    async submitAttempt(given) {
      await new Promise((r) => setTimeout(r, 300));
      const results = {};
      for (const [id, blank] of answers) results[id] = judge(given[id] ?? "", blank);
      const correct = Object.values(results).filter((v) => v === "correct").length;
      attemptNo += 1;
      return { attempt_id: `fake-${attemptNo}`, results, score: correct / answers.size };
    },
    get attemptCount() {
      return attemptNo;
    },
  };
}
