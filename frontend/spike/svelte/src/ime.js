// 한글 조합(IME) 보호. 순수 HTML/JS 후보와 같은 규칙이다(설계 3.2).
// 조합 중과, 조합을 끝낸 키가 확정 직후 한 번 더 오는 보통 Enter·Tab을 막는다.
const IME_RELEASE_MS = 100;
const ime = { composing: false, justEnded: false, timer: 0 };

export function trackComposition() {
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

export function blockedByIme(event) {
  return event.isComposing || event.keyCode === 229 || ime.composing || ime.justEnded;
}
