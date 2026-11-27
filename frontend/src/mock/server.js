// 가짜 서버(Mock). 실제 서버 API가 develop에 합쳐지기 전까지 api.js가 fetch 대신 이 handle()을 부른다.
// 응답 모양은 docs/design/data-contract.md 2절, 오류 모양은 3절을 따른다. 배포 때는 빠져도 되는 코드다.
//
// 주소 뒤에 ?scenario=이름 을 붙여 흐름을 고른다.
//   (없음)        queued → running → done
//   partial       queued → running → partial (2쪽 처리 실패)
//   failed        queued → running → failed (unreadable_pdf). 같은 PDF로 다시 만들어도 같아 다시 만들기가 없다
//   processing_failed queued → running → failed (processing_failed). 다시 만들기를 누를 수 있다
//   unauthorized  로그인 전 상태. 로그인 외 모든 요청이 401 unauthorized
//   encrypted_pdf · too_many_pages · daily_limit_exceeded · too_many_running · service_limit_exceeded
//                 업로드가 그 코드로 거절된다(계약 3절)
//   lost_response 업로드를 서버는 받았는데 응답이 끊긴다(키마다 처음 한 번). 같은 키로 다시 보내면 같은 세트가 온다
//   gateway_timeout 업로드를 서버는 받았는데 프록시가 본문 없는 504를 준다(키마다 처음 한 번). 다시 보내면 같은 세트
//   image_expired 끝난 세트를 처음 조회했을 때 받은 이미지 주소가 열리지 않는다(만료). 다시 조회하면 새 주소가 열린다
//   image_missing 이미지 주소가 늘 열리지 않는다. 화면은 한 번 다시 조회한 뒤 쪽마다 오류를 보인다
//   attempt_lost  세트마다 첫 답안 제출과 시도마다 첫 정답 공개가, 서버는 처리했는데 응답이 끊긴다. 다시 누르면 된다
//   session_expired 풀이 화면까지는 되고, 답안 제출과 정답 공개만 401 unauthorized(로그인이 풀린 경우)
// 처리 중(queued·running)인 세트에 답안을 내면 어느 시나리오든 409 not_ready다.
// 확장자가 .pdf가 아니면 415 not_pdf, 20MB를 넘으면 413 file_too_large는 시나리오와 상관없이 낸다.
// 같은 PDF를 다시 올렸을 때 기존 document_id를 돌려주는 것(계약 2절 POST /documents)은 흉내 내지 않는다.
//
// 데모 세트(업로드 없이 풀이 화면 보기): #/sets/6f1c2a4e-0000-4000-8000-000000000001

const DEMO_SET_ID = "6f1c2a4e-0000-4000-8000-000000000001";
const DEMO_DOCUMENT_ID = "0d0c0000-0000-4000-8000-000000000001";
const LATENCY_MS = 200;
const POLLS_UNTIL_FINISHED = 2; // 조회 1번째 queued, 2번째 running, 3번째부터 끝난 상태

const scenario = new URLSearchParams(globalThis.location?.search ?? "").get("scenario") ?? "done";

// 서버 설정 Settings.upload_max_bytes 기본값(#101)과 같게. 화면 쪽 값은 messages.js의 LIMITS.
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
// 시나리오 이름 → 업로드 거절. 문장은 서버가 줄 법한 예시이고, 화면은 이 문장을 그대로 보여 준다.
// "30쪽"은 서버 설정 upload_max_pages 기본값(#101)이다.
const UPLOAD_REJECTIONS = {
  encrypted_pdf: [422, "암호가 걸렸거나 스크립트가 든 PDF는 올릴 수 없어요."],
  too_many_pages: [422, "30쪽까지 올릴 수 있어요."],
  daily_limit_exceeded: [429, "오늘 처리할 수 있는 쪽 수를 넘었어요."],
  too_many_running: [429, "진행 중인 작업이 너무 많아요."],
  service_limit_exceeded: [503, "오늘은 더 만들 수 없어요."],
};

// 시나리오 이름 → 끝난 세트의 실패 사유(계약 2절 error.code).
const FAILURES = {
  failed: { code: "unreadable_pdf", message: "인식이 잘 안 됐어요, 다시 올려 주세요." },
  processing_failed: { code: "processing_failed", message: "처리하다 문제가 생겼어요. 다시 만들어 주세요." },
};

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
  loggedIn: scenario !== "unauthorized",
  sets: new Map([[DEMO_SET_ID, newSet(DEMO_DOCUMENT_ID, "auto")]]),
  documents: new Map([[DEMO_DOCUMENT_ID, { filename: "세포생물학-3주차.pdf", sets: [DEMO_SET_ID] }]]),
  attempts: new Map(), // attempt_id → {set_id, submitted_at, score, after_reveal, revealed_at}
  idempotency: new Map(), // "경로 키" → {fingerprint: 요청 내용, response: 처음 돌려준 응답}
  lostKeys: new Set(), // lost_response·gateway_timeout·attempt_lost 시나리오에서 응답을 한 번 끊은 키·요청
};

const ROUTES = [
  ["POST", /^\/auth\/login$/, login],
  ["POST", /^\/auth\/logout$/, () => { state.loggedIn = false; return reply(204); }],
  ["GET", /^\/documents$/, listDocuments],
  ["POST", /^\/documents$/, uploadDocument],
  ["DELETE", /^\/documents\/([^/]+)$/, deleteDocument],
  ["POST", /^\/documents\/([^/]+)\/blank-sets$/, remakeBlankSet],
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
    if (handler !== login && !state.loggedIn) return error(401, "unauthorized", "로그인이 필요해요.");
    return handler(init, ...match.slice(1));
  }
  return error(404, "not_found", "찾을 수 없어요.");
}

// ---- 핸들러 ----

function login(init) {
  const { login_id, password } = JSON.parse(init.body);
  if (!login_id || !password) {
    return error(401, "invalid_credentials", "아이디 또는 비밀번호가 맞지 않아요.");
  }
  state.loggedIn = true;
  return reply(204);
}

function listDocuments() {
  // [가정] 계약 2절은 "문서마다 세트 요약"만 정한다. 문서 쪽 필드 이름은 #54 응답이 나오면 맞춘다.
  const documents = [...state.documents].map(([document_id, doc]) => ({
    document_id,
    filename: doc.filename,
    blank_sets: doc.sets.map((id) => {
      const set = state.sets.get(id);
      return { blank_set_id: id, preset_applied: set.preset_applied, status: statusOf(set), created_at: set.created_at };
    }),
  }));
  return reply(200, documents);
}

function uploadDocument(init) {
  const file = init.body.get("file");
  const preset = init.body.get("preset") ?? "auto";
  // 실패한 요청은 키에 남기지 않는다. 남기면 PDF로 바꿔 같은 키로 다시 올려도 계속 415가 나온다.
  if (!file?.name?.toLowerCase().endsWith(".pdf")) return error(415, "not_pdf", "PDF 파일만 올릴 수 있어요.");
  if (file.size > MAX_UPLOAD_BYTES) return error(413, "file_too_large", "파일이 너무 커요.");
  if (scenario in UPLOAD_REJECTIONS) {
    const [status, message] = UPLOAD_REJECTIONS[scenario];
    return error(status, scenario, message);
  }
  // 지문은 Mock이라 파일 이름·크기·방식으로 대신한다(계약의 sha256은 서버 몫).
  const response = once(init, "POST /documents", `${file.name}:${file.size}:${preset}`, () => {
    const documentId = crypto.randomUUID();
    const setId = crypto.randomUUID();
    state.sets.set(setId, newSet(documentId, preset));
    state.documents.set(documentId, { filename: file.name, sets: [setId] });
    return reply(202, { document_id: documentId, blank_set_id: setId });
  });
  const key = init.headers["Idempotency-Key"];
  const cutOnce = ["lost_response", "gateway_timeout"].includes(scenario);
  if (cutOnce && response.ok && !state.lostKeys.has(key)) {
    state.lostKeys.add(key);
    if (scenario === "lost_response") throw new TypeError("Failed to fetch"); // fetch가 응답을 못 받았을 때와 같은 오류
    // 프록시(Tailscale 등)의 시간 초과는 계약 3절 모양이 아닌 HTML 본문으로 온다.
    return new Response("<html><body>504 Gateway Timeout</body></html>", { status: 504 });
  }
  return response;
}

function deleteDocument(_init, documentId) {
  const doc = state.documents.get(documentId);
  if (!doc) return notFound();
  // 지운 문서의 세트도 지운다. 계약 2절대로 삭제 뒤에는 세트 조회·답안 제출도 not_found다.
  for (const setId of doc.sets) state.sets.delete(setId);
  state.documents.delete(documentId);
  return reply(204);
}

function remakeBlankSet(init, documentId) {
  const doc = state.documents.get(documentId);
  if (!doc) return notFound();
  const preset = JSON.parse(init.body).preset ?? "auto";
  return once(init, `POST /documents/${documentId}/blank-sets`, preset, () => {
    const setId = crypto.randomUUID();
    state.sets.set(setId, newSet(documentId, preset));
    doc.sets.push(setId);
    return reply(202, { blank_set_id: setId });
  });
}

function getBlankSet(_init, setId) {
  const set = state.sets.get(setId);
  if (!set) return notFound();
  set.polls += 1;
  const status = statusOf(set);
  const total = PAGES.length;
  const body = {
    blank_set_id: setId,
    document_id: set.document_id, // 다시 만들기에 쓴다(#118)
    status,
    progress: { done: { queued: 0, running: 1 }[status] ?? total, total },
    preset_requested: set.preset_requested,
    preset_applied: status === "queued" ? null : set.preset_applied,
    preset_reason: status === "queued" ? null : "강의 슬라이드로 보여 개념 용어 방식을 골랐어요",
    pages: [],
  };
  if (status === "done" || status === "partial") set.finishedPolls += 1;
  // partial에서 실패한 쪽은 pages에 넣지 않는다(계약 2절 GET /blank-sets/{id}, #93).
  if (status === "done") body.pages = PAGES.map((p) => withImage(p, set));
  if (status === "partial") body.pages = PAGES.slice(0, 1).map((p) => withImage(p, set));
  if (status === "failed") body.error = FAILURES[scenario];
  return reply(200, body);
}

function submitAttempt(init, setId) {
  // 실제 서버처럼 로그인 확인을 404·409보다 먼저 한다.
  if (scenario === "session_expired") return sessionExpired();
  const set = state.sets.get(setId);
  if (!set) return notFound();
  if (["queued", "running"].includes(statusOf(set))) {
    return error(409, "not_ready", "아직 빈칸을 만드는 중이에요.");
  }
  const { answers } = JSON.parse(init.body);
  const blankIds = shownBlankIds(set);
  // 화면 시험용 가짜 채점: 대소문자·앞뒤 공백만 무시하고, 5자 이상 정답에서 한 글자 차이는 near다.
  // 진짜 판정은 서버 cloze/core/grade(#55)가 기준이다.
  const results = Object.fromEntries(blankIds.map((id) => [id, judge(answers[id] ?? "", ANSWERS.get(id))]));
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
  // 시도는 저장했는데 응답이 끊긴 경우. 다시 내면 시도가 하나 더 생긴다(계약상 제출에는 Idempotency-Key가 없다).
  if (cutOnce(`submit ${setId}`)) throw new TypeError("Failed to fetch");
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
  if (scenario === "session_expired") return sessionExpired();
  const attempt = state.attempts.get(attemptId);
  if (!attempt) return notFound();
  const set = state.sets.get(attempt.set_id);
  if (!set) return notFound(); // 문서를 지워 세트가 사라졌다
  attempt.revealed_at ??= new Date().toISOString();
  set.revealed = true; // 이후 시도는 after_reveal: true
  if (cutOnce(`reveal ${attemptId}`)) throw new TypeError("Failed to fetch");
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

/**
 * 조회할 때마다 새 서명이 붙는 것을 흉내 내, 주소 끝에 몇 번째 조회인지 붙인다(#로 붙여 그림에는 영향이 없다).
 * image_expired는 끝난 뒤 첫 조회의 주소만, image_missing은 모든 주소를 열리지 않게 만든다.
 */
function withImage(p, set) {
  const broken = scenario === "image_missing" || (scenario === "image_expired" && set.finishedPolls === 1);
  const url = broken ? BROKEN_IMAGE : p.image_url;
  return { ...p, image_url: `${url}#poll-${set.polls}` };
}

// 열리지 않는 그림 주소. 실제로는 만료·위조된 서명이나 지워진 파일이다.
const BROKEN_IMAGE = "data:image/webp;base64,AAAA";

/** 실제 서명 URL 대신 쓰는 회색 그림(720×540). */
function placeholderImage(pageNo) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="720" height="540"><rect width="100%" height="100%" fill="#f4f4f5"/><text x="24" y="48" font-size="24" fill="#71717a">Mock page ${pageNo}</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

function newSet(documentId, preset) {
  return {
    document_id: documentId,
    preset_requested: preset,
    preset_applied: preset === "auto" ? "concept" : preset,
    created_at: new Date().toISOString(),
    polls: 0,
    finishedPolls: 0, // 끝난 상태로 조회된 횟수(image_expired 시나리오용)
    revealed: false,
  };
}

function statusOf(set) {
  if (set.polls <= 1) return "queued";
  if (set.polls <= POLLS_UNTIL_FINISHED) return "running";
  if (scenario === "partial") return "partial";
  return scenario in FAILURES ? "failed" : "done";
}

function shownBlankIds(set) {
  const pages = statusOf(set) === "partial" ? PAGES.slice(0, 1) : PAGES;
  return pages.flatMap((p) => p.blanks.map((b) => b.blank_id));
}

function judge(given, answer) {
  const norm = (s) => s.normalize("NFKC").trim().toLowerCase();
  const [a, b] = [norm(given), norm(answer)];
  if (a === "") return "wrong";
  if (a === b) return "correct";
  return b.length >= 5 && withinOneEdit(a, b) ? "near" : "wrong";
}

/** 글자 하나를 바꾸거나 넣거나 빼서 같아지는가(편집 거리 1). */
function withinOneEdit(a, b) {
  if (Math.abs(a.length - b.length) > 1) return false;
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i += 1;
  const rest = (x, skip) => x.slice(i + skip);
  if (a.length === b.length) return rest(a, 1) === rest(b, 1);
  return a.length > b.length ? rest(a, 1) === rest(b, 0) : rest(a, 0) === rest(b, 1);
}

/** attempt_lost 시나리오에서 이 이름의 요청을 처음 한 번만 끊는다. */
function cutOnce(name) {
  if (scenario !== "attempt_lost" || state.lostKeys.has(name)) return false;
  state.lostKeys.add(name);
  return true;
}

/**
 * 같은 경로에 같은 Idempotency-Key로 다시 오면 새로 만들지 않고 처음 응답을 그대로 돌려준다.
 * 같은 키에 다른 내용(fingerprint)이 오면 invalid_request로 거절한다(계약 2절 "중복 요청 방지").
 */
function once(init, route, fingerprint, create) {
  const key = init.headers["Idempotency-Key"];
  if (!key) return error(422, "invalid_request", "요청에 Idempotency-Key가 없어요.");
  const slot = `${route} ${key}`;
  const seen = state.idempotency.get(slot);
  if (seen && seen.fingerprint !== fingerprint) {
    return error(422, "invalid_request", "같은 키로 다른 내용을 보냈어요.");
  }
  if (!seen) state.idempotency.set(slot, { fingerprint, response: create() });
  return state.idempotency.get(slot).response.clone();
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

function sessionExpired() {
  return error(401, "unauthorized", "로그인이 필요해요.");
}
