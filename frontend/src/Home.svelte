<script>
  // 처음 화면: PDF 하나와 빈칸 방식을 받아 업로드하고, 받은 세트 화면으로 넘어간다(#35).
  import { ApiError, uploadDocument } from "./api.js";
  import { LIMITS, uploadErrorMessage, uploadMessage } from "./messages.js";

  // 값과 이름은 데이터 계약 1절 preset.
  const PRESETS = [
    ["auto", "자동"],
    ["concept", "개념 용어"],
    ["vocab", "어휘 문맥"],
    ["number", "수치·날짜"],
    ["definition", "정의"],
  ];

  let preset = $state("auto");
  let dragging = $state(false);
  let busy = $state(false);
  let problem = $state("");
  // 응답을 받지 못한 업로드. 서버는 받았을 수도 있으니, 다시 보낼 때 같은 Idempotency-Key를 써야 세트가 두 개 생기지 않는다.
  let retry = $state(null);
  let picker;

  function pick(files) {
    if (busy) return;
    retry = null;
    if (files.length !== 1) return (problem = uploadMessage("multiple_files"));
    const [file] = files;
    // 서버도 같은 검사를 한다. 여기서 먼저 거르는 것은 보내기 전에 바로 알려 주기 위해서다.
    if (!file.name.toLowerCase().endsWith(".pdf")) return (problem = uploadMessage("not_pdf"));
    if (file.size > LIMITS.maxMb * 1024 * 1024) return (problem = uploadMessage("file_too_large"));
    // 사용자 동작 한 번 = 키 하나(계약 2절 "중복 요청 방지").
    send({ file, preset, key: crypto.randomUUID() });
  }

  async function send(request) {
    busy = true;
    problem = "";
    retry = null;
    try {
      const { blank_set_id } = await uploadDocument(request.file, request.preset, request.key);
      location.hash = `#/sets/${blank_set_id}`;
    } catch (e) {
      problem = uploadErrorMessage(e);
      if (!(e instanceof ApiError)) retry = request;
    } finally {
      busy = false;
      picker.value = ""; // 같은 파일을 다시 골라도 change가 오게 한다
    }
  }

  function drop(event) {
    event.preventDefault();
    dragging = false;
    pick(event.dataTransfer.files);
  }
</script>

<!-- 상자 밖에 떨어뜨린 PDF를 브라우저가 열어 버리지 않게 막는다. -->
<svelte:window ondragover={(e) => e.preventDefault()} ondrop={(e) => e.preventDefault()} />

<header>
  <a class="logo" href="#/">Cloze</a>
</header>

<main>
  <h1>강의자료를 빈칸 문제로</h1>
  <p class="lead">PDF를 올리면 중요한 말을 가린 풀이 화면을 만들어 드려요.</p>

  <section class="card">
    <div
      class="drop"
      class:dragging
      role="region"
      aria-label="PDF 올리기"
      ondragover={(e) => {
        e.preventDefault();
        dragging = true;
      }}
      ondragleave={() => (dragging = false)}
      ondrop={drop}
    >
      <svg class="icon" viewBox="0 0 48 48" aria-hidden="true">
        <path d="M12 4h17l9 9v31H12z" fill="none" stroke="currentColor" stroke-width="2.5" />
        <path d="M24 34V20m-6 6 6-6 6 6" fill="none" stroke="currentColor" stroke-width="2.5" />
      </svg>
      <button class="primary" type="button" disabled={busy} onclick={() => picker.click()}>
        {busy ? "올리는 중…" : "PDF 올리기"}
      </button>
      <input
        bind:this={picker}
        type="file"
        accept="application/pdf,.pdf"
        hidden
        onchange={(e) => pick(e.currentTarget.files)}
      />
      <p class="muted">또는 여기에 PDF 파일을 끌어다 놓기</p>

      <fieldset disabled={busy}>
        <legend>빈칸 방식</legend>
        {#each PRESETS as [value, label] (value)}
          <label class="chip">
            <input type="radio" name="preset" {value} bind:group={preset} />
            <span>{label}</span>
          </label>
        {/each}
      </fieldset>
      {#if preset === "auto"}<p class="muted small">자료를 보고 알맞은 방식을 골라요.</p>{/if}

      <p class="muted small">PDF 1개, {LIMITS.maxMb}MB·{LIMITS.maxPages}쪽까지 올릴 수 있어요.</p>
      <!-- 실시간 영역은 글자가 바뀌기 전부터 DOM에 있어야 낭독된다. 비면 :empty로 숨긴다. -->
      <p class="problem" role="alert">{problem}</p>
      {#if retry}
        <button type="button" onclick={() => send(retry)}>다시 보내기</button>
      {/if}
    </div>
  </section>
</main>

<style>
  header {
    padding: 14px 24px;
  }
  .logo {
    font-weight: 700;
    font-size: 20px;
    color: var(--accent);
    text-decoration: none;
  }
  main {
    max-width: 880px;
    margin: 0 auto;
    padding: 24px 16px 48px;
    text-align: center;
  }
  h1 {
    font-size: clamp(28px, 5vw, 44px);
    margin: 16px 0 8px;
  }
  .lead {
    color: var(--muted);
    margin: 0 0 32px;
  }
  .card {
    background: var(--surface);
    border-radius: 16px;
    padding: 16px;
  }
  .drop {
    border: 2px dashed var(--line);
    border-radius: 12px;
    padding: 32px 16px;
  }
  .drop.dragging {
    border-color: var(--accent);
    background: var(--accent-weak);
  }
  .icon {
    width: 56px;
    height: 56px;
    color: var(--accent);
    display: block;
    margin: 0 auto 16px;
  }
  button {
    font: inherit;
    padding: 10px 18px;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: var(--surface);
    cursor: pointer;
  }
  .primary {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
    font-weight: 600;
    padding: 12px 28px;
  }
  button:disabled {
    opacity: 0.6;
    cursor: wait;
  }
  fieldset {
    border: 0;
    margin: 20px 0 4px;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 8px;
  }
  legend {
    width: 100%;
    margin-bottom: 8px;
    font-weight: 600;
  }
  .chip input {
    position: absolute;
    opacity: 0;
  }
  .chip span {
    display: inline-block;
    padding: 6px 14px;
    border: 1px solid var(--line);
    border-radius: 999px;
    color: var(--muted);
    cursor: pointer;
  }
  .chip input:checked + span {
    background: var(--accent-weak);
    border-color: var(--accent);
    color: var(--accent);
    font-weight: 600;
  }
  .chip input:focus-visible + span {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .muted {
    color: var(--muted);
  }
  .small {
    font-size: 14px;
    margin: 6px 0;
  }
  .problem {
    color: var(--danger);
  }
  .problem:empty {
    display: none;
  }
</style>
