// 화면 문구(업로드, 답안 제출·정답 공개·다시 만들기 오류와 시각 표시). 서버가 거절한 코드(계약 3절)는 서버 message를 그대로 쓴다.
// 실제 한도 값을 아는 쪽이 서버라서다. 화면 문구는 보내기 전에 화면이 직접 거르는 경우와 응답을 받지 못한 경우에만 쓴다.
import { ApiError } from "./api.js";

// 한도 값은 서버 설정 Settings.upload_max_bytes·upload_max_pages(#101, 신뢰성 설계 5절 [가설])가 기준이다.
// 서버 값이 바뀌면 여기와 mock/server.js의 MAX_UPLOAD_BYTES도 고친다.
export const LIMITS = { maxMb: 20, maxPages: 30 };

const BEFORE_SEND = {
  not_pdf: "PDF 파일만 올릴 수 있어요.",
  file_too_large: `${LIMITS.maxMb}MB 이하 PDF만 올릴 수 있어요.`,
  multiple_files: "PDF는 한 번에 하나씩 올릴 수 있어요.",
};

/** 보내기 전에 화면이 거른 경우의 문장. */
export const beforeSendMessage = (code) => BEFORE_SEND[code];

/**
 * 같은 Idempotency-Key로 다시 보내야 하는 오류인가. 응답이 끊겼거나(fetch 실패) 서버·프록시 오류(5xx)면
 * 서버가 세트를 이미 만들었을 수 있다. service_limit_exceeded(503)는 계약 3절의 업무 거절이라 제외한다.
 */
export function canResend(error) {
  if (!(error instanceof ApiError)) return true;
  return error.status >= 500 && error.code !== "service_limit_exceeded";
}

/** 요청 오류 → 화면 문장. 응답을 받지 못했으면 again(다시 할 일)을 붙이고, 서버가 거절했으면 서버 문장을 쓴다. */
function requestErrorMessage(error, again) {
  if (!(error instanceof ApiError)) return `연결이 끊겨 결과를 받지 못했어요. ${again}`;
  if (canResend(error)) return `서버에서 결과를 받지 못했어요. ${again}`;
  return error.message;
}

/** 업로드 오류 → 화면 문장. */
export const uploadErrorMessage = (error) => requestErrorMessage(error, "다시 보내 주세요.");

/** 버튼 동작(답안 제출·정답 공개·다시 만들기) 오류 → 화면 문장. */
export const actionErrorMessage = (error) => requestErrorMessage(error, "다시 눌러 주세요.");

const TIME = new Intl.DateTimeFormat("ko-KR", { dateStyle: "short", timeStyle: "short" });

/** 서버가 준 ISO 시각 → "26. 11. 28. 오후 3:05". 읽을 수 없는 값이면 빈 글자다(format은 Invalid Date에서 예외를 낸다). */
export function formatTime(iso) {
  const time = Date.parse(iso);
  return Number.isNaN(time) ? "" : TIME.format(time);
}
