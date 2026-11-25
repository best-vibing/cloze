// 한글 조합(IME) 보호. 스파이크(#57, spike/svelte/src/ime.js)에서 옮겼다. 규칙은 채점·입력칸 설계 3.2.
// 조합 중과, 조합을 끝낸 키가 확정 직후 한 번 더 오는 보통 Enter·Tab을 막는다.
const IME_RELEASE_MS = 100;
const ime = { composing: false, justEnded: false, timer: 0 };

let tracking = false;

/** 문서 전체의 조합 시작·끝을 지켜본다. 여러 번 불러도 한 번만 건다. */
export function trackComposition() {
  if (tracking) return;
  tracking = true;
  document.addEventListener("compositionstart", () => {
    ime.composing = true;
  });
  document.addEventListener("compositionend", () => {
    ime.composing = false;
    ime.justEnded = true;
    clearTimeout(ime.timer);
    ime.timer = setTimeout(() => (ime.justEnded = false), IME_RELEASE_MS);
  });
  // 조합을 끝낸 키(Enter·Tab)를 놓을 때만 푼다. Shift 같은 다른 키를 놓아도 풀리면,
  // 뒤이어 오는 중복 Enter를 막지 못한다.
  document.addEventListener("keyup", (event) => {
    if (event.key === "Enter" || event.key === "Tab") ime.justEnded = false;
  });
}

/** 이 키를 칸 이동으로 쓰면 안 되는가(조합 중이거나 막 끝났는가). */
export function blockedByIme(event) {
  return event.isComposing || event.keyCode === 229 || ime.composing || ime.justEnded;
}
