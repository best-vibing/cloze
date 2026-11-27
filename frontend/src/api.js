// 서버와 주고받는 코드는 이 파일에만 둔다. 화면 코드는 fetch를 직접 부르지 않고 아래 함수만 부른다.
// 요청·응답 모양은 docs/design/data-contract.md 2절(API)과 3절(오류 형식)을 따른다.

// Mock 여부. 개발 서버(npm run dev)는 Mock, 빌드(npm run build)는 실제 서버가 기본이다.
// 코드에 박아 두면 바꾸기를 잊은 채 빌드한 dist/에서도 Mock이 돈다. 바꾸려면 VITE_API_MOCK=1(켜기)·0(끄기).
// Mock도 같은 request()를 거쳐 응답 모양과 오류 처리가 같다.
const mockEnv = import.meta.env.VITE_API_MOCK;
export const isMock = mockEnv === undefined ? import.meta.env.DEV : mockEnv === "1";
// Mock이 아니면 불러오지 않아, 운영 빌드에는 mock/server.js가 들어가지 않는다.
const mock = isMock ? await import("./mock/server.js") : null;

// 로그인 상태를 묻는 API가 없어서(계약 2절), 화면은 401 unauthorized를 받고서야 로그인이 필요한 줄 안다.
// window 이벤트 두 개로 알린다. App은 로그인 창을 열고 닫고, 각 화면은 로그인 뒤 멈췄던 요청을 다시 한다.
export const LOGIN_NEEDED = "cloze-login-needed";
export const LOGGED_IN = "cloze-logged-in";

/** 서버가 돌려준 오류. 모양은 계약 3절의 {"error": {"code", "message"}}다. */
export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "ApiError";
    this.status = status; // 예: 401, 404, 409
    this.code = code; // 예: "unauthorized", "not_ready"
  }
}

/**
 * 요청 하나를 보내고 JSON 본문을 돌려준다. 204면 null이다.
 * @param {string} method  "GET", "POST", "DELETE"
 * @param {string} path    "/blank-sets/{id}" 같은 API 경로
 * @param {{json?: object, form?: FormData, headers?: object}} [options]
 */
async function request(method, path, { json, form, headers = {} } = {}) {
  const init = { method, headers: { ...headers }, credentials: "same-origin" };
  if (json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (form !== undefined) {
    init.body = form; // multipart 경계(boundary)는 브라우저가 붙인다
  }
  const res = isMock ? await mock.handle(method, path, init) : await fetch(path, init);
  if (res.status === 204) return null;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const error = body?.error ?? { code: "unknown", message: "잠시 후 다시 시도해 주세요." };
    // 세션 쿠키가 없거나 끊겼다. 화면마다 따로 처리하지 않고 App이 로그인 창을 띄운다.
    // 로그인 실패(invalid_credentials)는 코드가 달라 여기에 걸리지 않는다.
    if (error.code === "unauthorized") window.dispatchEvent(new Event(LOGIN_NEEDED));
    throw new ApiError(res.status, error.code, error.message);
  }
  return body;
}

// ---- 인증 (#35) ----
/** 로그인. 성공하면 LOGGED_IN을 알린다. 실패는 ApiError(invalid_credentials·too_many_login_attempts)다. */
export async function login(loginId, password) {
  await request("POST", "/auth/login", { json: { login_id: loginId, password } });
  window.dispatchEvent(new Event(LOGGED_IN));
}
/** 로그아웃. 성공하면 LOGIN_NEEDED를 알려 로그인 창이 뜬다. */
export async function logout() {
  await request("POST", "/auth/logout");
  window.dispatchEvent(new Event(LOGIN_NEEDED));
}

// ---- 문서 (#35) ----
/** 업로드. key는 사용자 동작 한 번마다 crypto.randomUUID()로 만들고, 재전송에는 같은 키를 쓴다. */
export function uploadDocument(file, preset = "auto", key = crypto.randomUUID()) {
  const form = new FormData();
  form.append("file", file);
  form.append("preset", preset);
  return request("POST", "/documents", { form, headers: { "Idempotency-Key": key } });
}
export const listDocuments = () => request("GET", "/documents");
export const deleteDocument = (documentId) => request("DELETE", `/documents/${documentId}`);
/** 다시 만들기. 업로드와 같은 이유로 Idempotency-Key를 붙인다. */
export const remakeBlankSet = (documentId, preset = "auto", key = crypto.randomUUID()) =>
  request("POST", `/documents/${documentId}/blank-sets`, {
    json: { preset },
    headers: { "Idempotency-Key": key },
  });

// ---- 빈칸 세트와 채점 (#58, #36) ----
/** 세트 조회. 폴링 겸용이며, 응답에는 정답이 없다. */
export const getBlankSet = (blankSetId) => request("GET", `/blank-sets/${blankSetId}`);
/** answers: {blank_id: 입력}. 처리 중인 세트면 409 not_ready. */
export const submitAttempt = (blankSetId, answers) =>
  request("POST", `/blank-sets/${blankSetId}/attempts`, { json: { answers } });
export const listAttempts = (blankSetId) => request("GET", `/blank-sets/${blankSetId}/attempts`);
/** 정답 공개. 공개 시각을 기록하므로 반드시 POST다(GET이면 새로고침만으로 기록이 생긴다). */
export const revealAttempt = (attemptId) => request("POST", `/attempts/${attemptId}/reveal`);
