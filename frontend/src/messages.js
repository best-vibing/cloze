// 업로드가 거절됐을 때 보여 줄 문장. 코드는 데이터 계약 3절, 한도 값은 신뢰성 설계 5절([가설])이 기준이다.
// 한도가 바뀌면 LIMITS 한 곳만 고친다. 서버가 실제로 거절하는 기준은 서버 설정이다.
import { ApiError } from "./api.js";

export const LIMITS = { maxMb: 20, maxPages: 30 };

const UPLOAD = {
  file_too_large: `${LIMITS.maxMb}MB 이하 PDF만 올릴 수 있어요.`,
  not_pdf: "PDF 파일만 올릴 수 있어요. 파일이 깨졌다면 다시 저장해서 올려 주세요.",
  encrypted_pdf: "암호가 걸렸거나 스크립트가 들어 있는 PDF는 올릴 수 없어요.",
  too_many_pages: `${LIMITS.maxPages}쪽까지 올릴 수 있어요. 나눠서 올려 주세요.`,
  daily_limit_exceeded: "오늘 만들 수 있는 쪽 수를 다 썼어요. 내일 다시 올려 주세요.",
  too_many_running: "빈칸을 만들고 있는 자료가 끝난 뒤에 다시 올려 주세요.",
  service_limit_exceeded: "오늘은 더 만들 수 없어요. 내일 다시 시도해 주세요.",
  unauthorized: "로그인이 필요해요.",
  multiple_files: "PDF는 한 번에 하나씩 올릴 수 있어요.",
};

const NETWORK = "연결이 끊겨 결과를 받지 못했어요. 다시 보내 주세요.";

/** 코드 하나의 문장. 화면이 서버에 보내기 전에 직접 거를 때(크기·확장자)도 같은 문장을 쓴다. */
export const uploadMessage = (code) => UPLOAD[code];

/** 업로드 오류 → 문장. 목록에 없는 코드는 서버가 준 문장을 그대로 쓴다(계약 3절: message는 사용자용 문장). */
export function uploadErrorMessage(error) {
  if (!(error instanceof ApiError)) return NETWORK;
  return UPLOAD[error.code] ?? error.message;
}
