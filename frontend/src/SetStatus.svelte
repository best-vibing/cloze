<script>
  // 세트 하나를 2초마다 조회해 상태와 진행률을 보여 주고, 끝나면(done·partial) 쪽마다 입력칸을 겹쳐 그린다(#58).
  // 답안 제출 → 칸별 결과·점수 → 정답 공개 → 다시 풀기(#36)도 여기서 한다. 규칙은 채점·입력칸 설계 2절.
  // 서버와는 api.js로만 주고받는다. fetch를 여기서 부르지 않는다.
  import { onMount, tick } from "svelte";
  import { ApiError, getBlankSet, revealAttempt, submitAttempt } from "./api.js";
  import BlankPage from "./BlankPage.svelte";
  import { blockedByIme, trackComposition } from "./ime.js";
  import { attemptErrorMessage } from "./messages.js";

  let { setId } = $props();

  const POLL_MS = 2000;
  const LABEL = {
    queued: "순서를 기다리는 중",
    running: "빈칸을 만드는 중",
    done: "완료",
    partial: "일부 쪽만 완료",
    failed: "만들지 못했어요",
  };
  const NETWORK = "연결에 문제가 있어요. 잠시 후 다시 시도해 주세요.";

  let blankSet = $state(null);
  let problem = $state("");
  let values = $state({}); // blank_id → 입력. 제출할 때 이 값을 보낸다. 이미지 주소를 새로 받아도 남는다.
  let hint = $state("");
  let broken = $state({}); // page_no → true: 새 주소로도 그림을 못 불러온 쪽

  const isRunning = (set) => ["queued", "running"].includes(set.status);
  const finished = $derived(blankSet && !isRunning(blankSet));
  const pages = $derived(
    finished && !blankSet.error ? [...blankSet.pages].sort((a, b) => a.page_no - b.page_no) : [],
  );
  const blankCount = $derived(pages.reduce((n, p) => n + p.blanks.length, 0));
  // Enter 이동 순서: 쪽 순서 → 쪽 안에서 order. 그림을 못 불러온 쪽의 칸은 그리지 않으니 건너뛴다.
  const order = $derived(
    pages
      .filter((p) => !broken[p.page_no])
      .flatMap((p) => [...p.blanks].sort((a, b) => a.order - b.order).map((b) => b.blank_id)),
  );

  onMount(() => {
    trackComposition();
    let timer;
    // 요청을 기다리는 동안 화면이 사라지면, 응답이 온 뒤 타이머를 다시 걸지 않는다.
    let stopped = false;
    async function poll() {
      try {
        blankSet = await getBlankSet(setId);
        problem = "";
        if (!isRunning(blankSet)) return;
      } catch (e) {
        // 서버 문장(message)은 텍스트로만 넣는다. Svelte {…}는 HTML로 해석하지 않는다.
        problem = e instanceof ApiError ? e.message : NETWORK;
        if (e instanceof ApiError && e.status < 500) return; // 401·404는 다시 물어봐도 같다
      }
      if (!stopped) timer = setTimeout(poll, POLL_MS);
    }
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  });

  // ---- 이미지 주소 만료 (계약 2절: 실패하면 세트를 한 번 다시 조회해 새 주소를 받는다) ----
  // <img>는 실패 이유(만료·없는 파일)를 알려 주지 않는다. 그래서 쪽마다 "새 주소로 한 번 다시"만 하고,
  // 그 주소로도 실패하면 그 쪽에 오류를 보인다. 성공하면 표시를 지워, 나중에 또 만료돼도 다시 받을 수 있다.
  const retried = new Set(); // 새 주소를 받은 뒤 아직 성공하지 못한 쪽
  let refreshing = null; // 진행 중인 재조회. 여러 쪽이 한꺼번에 실패해도 한 번만 묻는다.

  function onimageload(pageNo) {
    retried.delete(pageNo);
  }

  function onimageerror(pageNo) {
    if (retried.has(pageNo)) {
      broken[pageNo] = true;
      return;
    }
    retried.add(pageNo);
    refreshImages();
  }

  /** "다시 불러오기" 버튼. 사용자가 누를 때만 다시 시도하므로 무한히 반복되지 않는다. */
  function onretry(pageNo) {
    delete broken[pageNo];
    retried.add(pageNo);
    refreshImages();
  }

  function refreshImages() {
    refreshing ??= (async () => {
      const before = new Map(pages.map((p) => [p.page_no, p.image_url]));
      try {
        const set = await getBlankSet(setId);
        // 끝난 세트가 다시 처리 중이 될 일은 없지만, 그렇게 오면 지금 그림을 지우지 않고 그대로 둔다.
        if (!isRunning(set)) blankSet = set;
        problem = "";
      } catch (e) {
        problem = e instanceof ApiError ? e.message : NETWORK;
      }
      // 주소가 그대로이거나 쪽이 사라졌으면 <img>가 다시 받지 않으므로, 기다리지 않고 바로 오류로 보인다.
      for (const pageNo of retried) {
        const now = pages.find((p) => p.page_no === pageNo);
        if (!now || now.image_url === before.get(pageNo)) broken[pageNo] = true;
      }
      refreshing = null;
    })();
  }

  /** Enter는 다음 칸, Shift+Enter는 이전 칸. Tab은 브라우저가 DOM 순서(= order)로 옮긴다. 조합 중 Enter·Tab은 막는다. */
  function onkeydown(event, blankId) {
    if (event.key !== "Enter" && event.key !== "Tab") return;
    const ime = blockedByIme(event);
    if (ime || event.key === "Enter") event.preventDefault();
    if (ime || event.key !== "Enter") return;
    const next = order[order.indexOf(blankId) + (event.shiftKey ? -1 : 1)];
    if (next) document.getElementById(`blank-${next}`)?.focus();
  }

  function onfocus(blank) {
    hint = blank.hint ? `힌트: ${blank.hint}` : "";
  }

  // ---- 제출·정답 공개·다시 풀기 (#36, 계약 2절 attempts) ----
  // 제출과 정답 공개에는 횟수 제한이 없다. 정답을 본 뒤의 시도인지(after_reveal)는 서버가 정해서 돌려준다.
  let attempt = $state(null); // 마지막 제출 결과 {attempt_id, results, score, after_reveal}. null이면 푸는 중
  let answers = $state(null); // 공개한 정답 {blank_id: 정답}. null이면 아직 안 봤다
  let showAnswers = $state(false); // 칸에 정답을 보이는 중인가(false면 내 답)
  let busy = $state(""); // "submit" · "reveal": 요청을 기다리는 중. 두 번 눌러 두 번 보내지 않게 막는다
  let asking = $state(false); // 빈 칸이 있어 "그대로 제출할까요?"를 묻는 중
  let actionProblem = $state("");

  // 그림을 못 불러온 쪽의 칸도 보낸다. 입력값은 values에 남아 있고, 빠지면 서버가 그 칸을 틀림으로 센다.
  const blankIds = $derived(pages.flatMap((p) => p.blanks.map((b) => b.blank_id)));
  const emptyCount = $derived(blankIds.filter((id) => !(values[id] ?? "").trim()).length);
  const tally = $derived(
    attempt
      ? Object.values(attempt.results).reduce((n, r) => ({ ...n, [r]: (n[r] ?? 0) + 1 }), {})
      : {},
  );

  async function submit(force) {
    if (busy) return;
    // 빈 칸은 틀림이 된다. 실수로 누른 것일 수 있어 한 번 묻는다.
    if (emptyCount > 0 && !force) {
      asking = true;
      return;
    }
    asking = false;
    busy = "submit";
    actionProblem = "";
    try {
      const sent = Object.fromEntries(blankIds.map((id) => [id, values[id] ?? ""]));
      attempt = await submitAttempt(setId, sent);
    } catch (e) {
      // 응답이 끊겼으면 서버는 시도를 저장했을 수 있다. 다시 내면 시도가 하나 더 쌓일 뿐 점수는 같다.
      actionProblem = attemptErrorMessage(e);
    } finally {
      busy = "";
    }
  }

  async function reveal() {
    if (busy || !attempt) return;
    busy = "reveal";
    actionProblem = "";
    try {
      const revealed = await revealAttempt(attempt.attempt_id);
      // 계약상 늘 오지만, 빠지면 칸을 그리다 깨지므로 빈 정답표로 둔다(칸은 빈 글자로 보인다).
      answers = revealed?.answers ?? {};
      showAnswers = true;
    } catch (e) {
      actionProblem = attemptErrorMessage(e);
    } finally {
      busy = "";
    }
  }

  /** 같은 세트로 새로 푼다. 입력을 비우고 첫 칸으로 간다. 정답을 봤으면 다음 시도는 서버가 복습으로 기록한다. */
  async function retry() {
    if (busy) return;
    attempt = null;
    answers = null;
    showAnswers = false;
    asking = false;
    actionProblem = "";
    values = {};
    await tick();
    document.getElementById(`blank-${order[0]}`)?.focus();
  }
</script>

<main>
  <p><a href="#/">← 처음 화면</a></p>
  <!-- 실시간 영역은 글자가 바뀌기 전부터 DOM에 있어야 낭독된다. 비면 :empty로 숨긴다. -->
  <p class="problem" role="alert">{problem}</p>
  {#if blankSet}
    <section aria-live="polite">
      <p><strong>{LABEL[blankSet.status] ?? blankSet.status}</strong></p>
      {#if !finished}
        <progress max={blankSet.progress.total} value={blankSet.progress.done}></progress>
        <span>처리 {blankSet.progress.done} / {blankSet.progress.total}쪽</span>
      {/if}
      {#if blankSet.preset_reason}<p>{blankSet.preset_reason}</p>{/if}
      {#if blankSet.error}<p class="problem">{blankSet.error.message}</p>{/if}
      {#if finished && !blankSet.error}
        <!-- progress는 처리를 시도한 쪽, pages는 풀 수 있는 쪽이라 partial이면 둘이 다르다(계약 2절). -->
        {#if pages.length === 0}
          <p>풀 수 있는 쪽이 없어요. 다른 PDF로 다시 올려 주세요.</p>
        {:else if blankSet.status === "partial"}
          <p>
            {blankSet.progress.total}쪽 중 {pages.length}쪽만 만들었어요. 빈칸 {blankCount}개를 받았어요.
          </p>
        {:else}
          <p>{pages.length}쪽, 빈칸 {blankCount}개를 받았어요.</p>
        {/if}
      {/if}
    </section>
  {:else if !problem}
    <p>불러오는 중…</p>
  {/if}
</main>

{#if pages.length}
  <div class="pages">
    {#each pages as page (page.page_no)}
      <BlankPage
        {page}
        bind:values
        results={attempt?.results ?? null}
        {answers}
        {showAnswers}
        broken={broken[page.page_no] ?? false}
        {onkeydown}
        {onfocus}
        {onimageload}
        {onimageerror}
        {onretry}
      />
    {/each}
  </div>
  <footer class="bar">
    <p class="hint" role="status">{hint}</p>
    <div class="summary" aria-live="polite">
      {#if attempt}
        <p>
          <strong>{Math.round(attempt.score * 100)}점</strong>
          · 맞음 {tally.correct ?? 0} · 거의 맞음 {tally.near ?? 0} · 틀림 {tally.wrong ?? 0}
        </p>
        {#if tally.near}<p class="muted">거의 맞음(한 글자 차이)은 점수에 넣지 않았어요.</p>{/if}
        {#if attempt.after_reveal}<p class="muted">정답을 본 뒤의 풀이라 복습으로 기록했어요.</p>{/if}
      {/if}
    </div>
    <p class="problem" role="alert">{actionProblem}</p>
    <div class="actions">
      {#if !attempt}
        {#if asking && emptyCount > 0}
          <span>빈칸 {emptyCount}개가 비어 있어요. 빈 칸은 틀림이 돼요.</span>
          <button type="button" onclick={() => (asking = false)}>계속 풀기</button>
          <button type="button" class="primary" disabled={busy !== ""} onclick={() => submit(true)}>
            {busy === "submit" ? "채점하는 중…" : "그대로 제출"}
          </button>
        {:else}
          <button type="button" class="primary" disabled={busy !== ""} onclick={() => submit(false)}>
            {busy === "submit" ? "채점하는 중…" : "제출"}
          </button>
        {/if}
      {:else}
        {#if answers === null}
          <button type="button" class="primary" disabled={busy !== ""} onclick={reveal}>
            {busy === "reveal" ? "정답을 불러오는 중…" : "정답 보기"}
          </button>
        {:else}
          <div class="toggle" role="group" aria-label="칸에 보일 글자">
            <button type="button" aria-pressed={!showAnswers} onclick={() => (showAnswers = false)}>내 답</button>
            <button type="button" aria-pressed={showAnswers} onclick={() => (showAnswers = true)}>정답</button>
          </div>
        {/if}
        <button type="button" disabled={busy !== ""} onclick={retry}>다시 풀기</button>
      {/if}
    </div>
  </footer>
{/if}

<style>
  main {
    max-width: 720px;
    margin: 0 auto;
    padding: 16px;
  }
  progress {
    width: 240px;
    vertical-align: middle;
  }
  .problem {
    color: var(--danger);
  }
  .problem:empty {
    display: none;
  }
  .pages {
    display: grid;
    gap: 16px;
    max-width: 1100px;
    margin: 0 auto;
    padding: 0 16px 16px;
  }
  /* 아래 막대: 힌트, 채점 결과, 제출·정답 보기·다시 풀기. 쪽을 내려 봐도 늘 보인다. */
  .bar {
    position: sticky;
    bottom: 0;
    display: grid;
    gap: 6px;
    padding: 8px 16px;
    background: var(--surface);
    border-top: 1px solid var(--line);
    font-size: 0.9rem;
  }
  .bar p {
    margin: 0;
  }
  .hint:empty,
  .summary:empty {
    display: none;
  }
  .muted {
    color: var(--muted);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  button {
    font: inherit;
    padding: 8px 16px;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
  }
  .primary {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
    font-weight: 600;
  }
  button:disabled {
    opacity: 0.6;
    cursor: wait;
  }
  .toggle {
    display: inline-flex;
  }
  .toggle button:first-child {
    border-radius: 8px 0 0 8px;
  }
  .toggle button:last-child {
    border-radius: 0 8px 8px 0;
    border-left: 0;
  }
  .toggle button[aria-pressed="true"] {
    background: var(--accent-weak);
    border-color: var(--accent);
    font-weight: 600;
  }
</style>
