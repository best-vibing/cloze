// 가짜 서버(Mock). 실제 서버 API가 develop에 합쳐지기 전까지 api.js가 fetch 대신 이 handle()을 부른다.
// 응답 모양은 docs/design/data-contract.md 2절, 오류 모양은 3절을 따른다. 배포 때는 빠져도 되는 코드다.
//
// 주소 뒤에 ?scenario=이름 을 붙여 흐름을 고른다.
//   (없음)        queued → running → done
//   partial       queued → running → partial (2쪽 처리 실패)
//   failed        queued → running → failed (unreadable_pdf)
// 처리 중(queued·running)인 세트에 답안을 내면 어느 시나리오든 409 not_ready다.

export const DEMO_SET_ID = "6f1c2a4e-0000-4000-8000-000000000001";
const LATENCY_MS = 200;
const POLLS_UNTIL_FINISHED = 2; // 조회 1번째 queued, 2번째 running, 3번째부터 끝난 상태

const scenario = new URLSearchParams(globalThis.location?.search ?? "").get("scenario") ?? "done";

// 정답은 이 안에만 둔다. 조회 응답(getBlankSet)에는 절대 넣지 않는다(D2).
const ANSWERS = new Map([
  ["9a2e0000-0000-4000-8000-000000000001", "mitochondria"],
  ["9a2e0000-0000-4000-8000-000000000002", "ATP"],
  ["9a2e0000-0000-4000-8000-000000000003", "ribosome"],
]);

// 화면에 보내도 되는 쪽 정보(계약 2절 pages). 정답 필드가 없다.
const PAGES = [
  page(1, [
    blank("9a2e0000-0000-4000-8000-000000000001", 1, [0.115, 0.305, 0.295, 0.355], "세포 소기관"),
    blank("9a2e0000-0000-4000-8000-000000000002", 2, [0.4, 0.505, 0.52, 0.555], null),
  ]),
  page(2, [blank("9a2e0000-0000-4000-8000-000000000003", 1, [0.2, 0.2, 0.36, 0.25], null)]),
];

const state = {
  sets: new Map([[DEMO_SET_ID, newSet("auto")]]),
  attempts: new Map(), // attempt_id → {set_id, submitted_at, score, after_reveal, revealed_at}
};

const ROUTES = [
  ["GET", /^\/blank-sets\/([^/]+)$/, getBlankSet],
  ["POST", /^\/blank-sets\/([^/]+)\/attempts$/, submitAttempt],
  ["GET", /^\/blank-sets\/([^/]+)\/attempts$/, listAttempts],
  ["POST", /^\/attempts\/([^/]+)\/reveal$/, revealAttempt],
];

/** fetch(path, init)처럼 불러서 Response를 받는다. api.js의 request()가 실제 서버 응답과 똑같이 다룬다. */
export async function handle(method, path, init) {
  await new Promise((r) => setTimeout(r, LATENCY_MS));
  for (const [m, pattern, handler] of ROUTES) {
    const match = method === m && path.match(pattern);
    if (!match) continue;
    return handler(init, ...match.slice(1));
  }
  return error(404, "not_found", "찾을 수 없어요.");
}

// ---- 핸들러 ----

function getBlankSet(_init, setId) {
  const set = state.sets.get(setId);
  if (!set) return notFound();
  set.polls += 1;
  const status = statusOf(set);
  const total = PAGES.length;
  const body = {
    blank_set_id: setId,
    status,
    progress: { done: { queued: 0, running: 1 }[status] ?? total, total },
    preset_requested: set.preset_requested,
    preset_applied: status === "queued" ? null : set.preset_applied,
    preset_reason: status === "queued" ? null : "강의 슬라이드로 보여 개념 용어 방식을 골랐어요",
    pages: [],
  };
  if (status === "done") body.pages = PAGES;
  // [가정] partial에서 실패한 쪽은 pages에 넣지 않는다. 표시 방법은 팀 회의에서 정한다.
  if (status === "partial") body.pages = PAGES.slice(0, 1);
  if (status === "failed") {
    body.error = { code: "unreadable_pdf", message: "인식이 잘 안 됐어요, 다시 올려 주세요." };
  }
  return reply(200, body);
}

function submitAttempt(init, setId) {
  const set = state.sets.get(setId);
  if (!set) return notFound();
  if (["queued", "running"].includes(statusOf(set))) {
    return error(409, "not_ready", "아직 빈칸을 만드는 중이에요.");
  }
  const { answers } = JSON.parse(init.body);
  const blankIds = shownBlankIds(set);
  // 화면 시험용 가짜 채점: 대소문자·앞뒤 공백만 무시한다. 진짜 판정은 서버 cloze/core/grade(#55)가 기준이다.
  const results = Object.fromEntries(
    blankIds.map((id) => [id, same(answers[id] ?? "", ANSWERS.get(id)) ? "correct" : "wrong"]),
  );
  const correct = Object.values(results).filter((r) => r === "correct").length;
  const attempt = {
    attempt_id: crypto.randomUUID(),
    set_id: setId,
    submitted_at: new Date().toISOString(),
    score: blankIds.length ? correct / blankIds.length : 0,
    after_reveal: set.revealed,
    revealed_at: null,
  };
  state.attempts.set(attempt.attempt_id, attempt);
  const { attempt_id, score, after_reveal } = attempt;
  return reply(200, { attempt_id, results, score, after_reveal });
}

function listAttempts(_init, setId) {
  if (!state.sets.has(setId)) return notFound();
  const list = [...state.attempts.values()]
    .filter((a) => a.set_id === setId)
    .map(({ attempt_id, submitted_at, score, after_reveal, revealed_at }) => ({
      attempt_id, submitted_at, score, after_reveal, revealed_at,
    }));
  return reply(200, list);
}

function revealAttempt(_init, attemptId) {
  const attempt = state.attempts.get(attemptId);
  if (!attempt) return notFound();
  const set = state.sets.get(attempt.set_id);
  attempt.revealed_at ??= new Date().toISOString();
  set.revealed = true; // 이후 시도는 after_reveal: true
  const answers = Object.fromEntries(shownBlankIds(set).map((id) => [id, ANSWERS.get(id)]));
  return reply(200, { answers });
}

// ---- 도우미 ----

function page(pageNo, blanks) {
  return { page_no: pageNo, image_url: placeholderImage(pageNo), width_pt: 720, height_pt: 540, blanks };
}

function blank(blankId, order, bbox, hint) {
  return {
    blank_id: blankId,
    order,
    bbox,
    type: "term",
    hint,
    style: { font_size_ratio: 0.025, font_color: "#1a1a1a", bg_color: "#ffffff" },
  };
}

/** 실제 서명 URL 대신 쓰는 회색 그림(720×540). */
function placeholderImage(pageNo) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="720" height="540"><rect width="100%" height="100%" fill="#f4f4f5"/><text x="24" y="48" font-size="24" fill="#71717a">Mock page ${pageNo}</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

function newSet(preset) {
  return {
    preset_requested: preset,
    preset_applied: preset === "auto" ? "concept" : preset,
    created_at: new Date().toISOString(),
    polls: 0,
    revealed: false,
  };
}

function statusOf(set) {
  if (set.polls <= 1) return "queued";
  if (set.polls <= POLLS_UNTIL_FINISHED) return "running";
  return ["partial", "failed"].includes(scenario) ? scenario : "done";
}

function shownBlankIds(set) {
  const pages = statusOf(set) === "partial" ? PAGES.slice(0, 1) : PAGES;
  return pages.flatMap((p) => p.blanks.map((b) => b.blank_id));
}

function same(given, answer) {
  const norm = (s) => s.normalize("NFKC").trim().toLowerCase();
  return norm(given) !== "" && norm(given) === norm(answer);
}

function reply(status, body) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
  });
}

function error(status, code, message) {
  return reply(status, { error: { code, message } });
}

function notFound() {
  return error(404, "not_found", "찾을 수 없어요.");
}
