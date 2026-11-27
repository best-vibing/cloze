<script>
  // 로그인 창(#35). 가입 화면은 없다. 계정은 팀이 CLI로 만든다(계약 2절 계정 관리).
  // App이 지금 화면 위에 띄우므로, 로그인하면 쓰던 화면(입력한 답 포함)으로 그대로 돌아간다.
  import { onMount } from "svelte";
  import { ApiError, login } from "./api.js";

  let loginId = $state("");
  let password = $state("");
  let busy = $state(false);
  let problem = $state("");
  let first;
  // 서버가 받는 최대 길이(#125 LoginBody). 넘기면 서버가 invalid_request로 거절하므로 입력에서 먼저 막는다.
  const MAX_ID = 64;
  const MAX_PASSWORD = 256;

  onMount(() => first.focus());

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    busy = true;
    problem = "";
    try {
      // 아이디 앞뒤에 실수로 들어간 공백만 뗀다. 비밀번호는 공백도 글자라 그대로 보낸다.
      await login(loginId.trim(), password);
      password = "";
    } catch (e) {
      // invalid_credentials·too_many_login_attempts는 서버 문장을 쓴다. 아이디가 없는지 비밀번호가 틀렸는지는
      // 서버가 일부러 구분하지 않으므로 화면도 구분하지 않는다.
      problem = e instanceof ApiError ? e.message : "연결이 끊겼어요. 잠시 후 다시 시도해 주세요.";
      password = "";
    } finally {
      busy = false;
    }
  }
</script>

<!-- 밑의 화면은 App이 inert로 막는다. 로그인 말고는 할 수 있는 일이 없어 닫기 버튼은 두지 않는다. -->
<div class="backdrop" role="dialog" aria-modal="true" aria-labelledby="login-title">
  <form class="card" onsubmit={submit}>
    <img src="/logo.webp" alt="Cloze" height="40" />
    <h1 id="login-title">로그인</h1>
    <label>
      아이디
      <input
        bind:this={first}
        bind:value={loginId}
        autocomplete="username"
        maxlength={MAX_ID}
        required
      />
    </label>
    <label>
      비밀번호
      <input
        type="password"
        bind:value={password}
        autocomplete="current-password"
        maxlength={MAX_PASSWORD}
        required
      />
    </label>
    <!-- 실시간 영역은 글자가 바뀌기 전부터 DOM에 있어야 낭독된다. 비면 :empty로 숨긴다. -->
    <p class="problem" role="alert">{problem}</p>
    <button class="primary" type="submit" disabled={busy}>{busy ? "확인하는 중…" : "로그인"}</button>
    <p class="muted">계정은 팀에 요청해 주세요.</p>
  </form>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    display: grid;
    place-items: center;
    padding: 16px;
    background: var(--bg);
    z-index: 10;
  }
  .card {
    display: grid;
    gap: 12px;
    width: min(360px, 100%);
    padding: 24px;
    background: var(--surface);
    border-radius: 16px;
  }
  img {
    height: 40px;
    width: auto;
    justify-self: start;
  }
  h1 {
    font-size: 1.4rem;
    margin: 0;
  }
  label {
    display: grid;
    gap: 4px;
    font-size: 0.9rem;
  }
  input {
    font: inherit;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 8px;
  }
  .primary {
    font: inherit;
    padding: 12px;
    border-radius: 8px;
    border: 1px solid var(--accent);
    background: var(--accent);
    color: #fff;
    font-weight: 600;
    cursor: pointer;
  }
  .primary:disabled {
    opacity: 0.6;
    cursor: wait;
  }
  p {
    margin: 0;
  }
  .problem {
    color: var(--danger);
  }
  .problem:empty {
    display: none;
  }
  .muted {
    color: var(--muted);
    font-size: 0.85rem;
  }
</style>
