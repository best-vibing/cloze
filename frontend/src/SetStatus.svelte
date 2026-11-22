<script>
  // 세트 하나를 2초마다 조회해 상태와 진행률을 보여 주고, 끝나면(done·partial) 쪽마다 입력칸을 겹쳐 그린다(#58).
  // 서버와는 api.js로만 주고받는다. fetch를 여기서 부르지 않는다. 제출·정답 공개는 #36에서 붙인다.
  import { onMount } from "svelte";
  import { ApiError, getBlankSet } from "./api.js";
  import BlankPage from "./BlankPage.svelte";
  import { blockedByIme, trackComposition } from "./ime.js";

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
  let values = $state({}); // blank_id → 입력. 제출(#36)할 때 이 값을 보낸다. 이미지 주소를 새로 받아도 남는다.
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
        broken={broken[page.page_no] ?? false}
        {onkeydown}
        {onfocus}
        {onimageload}
        {onimageerror}
        {onretry}
      />
    {/each}
  </div>
  <footer class="hint">
    <p role="status">{hint}</p>
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
  .hint {
    position: sticky;
    bottom: 0;
    padding: 6px 16px;
    background: var(--surface);
    border-top: 1px solid var(--line);
    font-size: 0.9rem;
  }
  .hint p {
    margin: 0;
  }
  .hint:has(p:empty) {
    display: none;
  }
</style>
