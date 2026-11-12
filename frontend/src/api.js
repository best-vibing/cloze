// 서버와 주고받는 코드는 이 파일에만 둔다. 화면 코드는 fetch를 직접 부르지 않고 아래 함수만 부른다.
// 요청·응답 모양은 docs/design/data-contract.md 2절(API)과 3절(오류 형식)을 따른다.
import * as mock from "./mock/server.js";

// 원터치 스위치: 실제 서버에 연결할 때 false로 바꾼다. Mock도 같은 request()를 거쳐 응답 모양과 오류 처리가 같다.
export const isMock = true;
/** Mock일 때 주소에 ?set= 이 없으면 여는 연습용 세트. 실제 서버에서는 없다. */
export const demoSetId = isMock ? mock.DEMO_SET_ID : null;

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
    throw new ApiError(res.status, error.code, error.message);
  }
  return body;
}

// ---- 빈칸 세트와 채점 (#58, #36) ----
/** 세트 조회. 폴링 겸용이며, 응답에는 정답이 없다. */
export const getBlankSet = (blankSetId) => request("GET", `/blank-sets/${blankSetId}`);
/** answers: {blank_id: 입력}. 처리 중인 세트면 409 not_ready. */
export const submitAttempt = (blankSetId, answers) =>
  request("POST", `/blank-sets/${blankSetId}/attempts`, { json: { answers } });
export const listAttempts = (blankSetId) => request("GET", `/blank-sets/${blankSetId}/attempts`);
/** 정답 공개. 공개 시각을 기록하므로 반드시 POST다(GET이면 새로고침만으로 기록이 생긴다). */
export const revealAttempt = (attemptId) => request("POST", `/attempts/${attemptId}/reveal`);
