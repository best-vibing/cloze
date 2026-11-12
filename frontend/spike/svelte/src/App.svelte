<script>
  // 프론트 스파이크(#57) — Svelte 후보. vanilla 후보와 같은 기능을 상태($state)로 다시 만들었다.
  import { onMount } from "svelte";
  import { FIXTURES, createFakeServer, readFixture, toApiPage } from "./data.js";
  import { blockedByIme, trackComposition } from "./ime.js";
  import Page from "./Page.svelte";

  let pages = $state([]); // 화면에 쓰는 쪽(정답 없음)
  let values = $state({}); // blank_id → 입력
  let results = $state({}); // blank_id → correct / near / wrong
  let status = $state("");
  let summary = ""; // 마지막 채점 결과 한 줄
  let hint = $state("");
  let submitting = $state(false);
  let debug = $state(false);
  let server;

  const order = $derived(pages.flatMap((p) => p.blanks.map((b) => b.blank_id)));

  onMount(async () => {
    trackComposition();
    const read = [];
    for (const [i, f] of FIXTURES.entries()) {
      read.push({ page: readFixture(await (await fetch(f.json)).json(), i + 1), image: f.image });
    }
    server = createFakeServer(read.map((r) => r.page));
    pages = read.map((r) => toApiPage(r.page, r.image));
    window.spikeServer = server; // 스파이크 확인용
  });

  /** Enter는 다음 칸, Shift+Enter는 이전 칸, 마지막 칸 Enter는 정답 확인. 조합 중 Enter·Tab은 막는다. */
  function onkeydown(event, id) {
    if (event.key !== "Enter" && event.key !== "Tab") return;
    if (blockedByIme(event) || event.key === "Enter") event.preventDefault();
    if (blockedByIme(event) || event.key !== "Enter") return;
    const index = order.indexOf(id);
    if (!event.shiftKey && index === order.length - 1) return submit();
    document.getElementById(`blank-${order[index + (event.shiftKey ? -1 : 1)]}`)?.focus();
  }

  /** 응답을 기다리는 동안에는 다시 제출하지 않는다. */
  async function submit() {
    if (submitting) return;
    // 마지막 채점 뒤 고친 칸이 없으면(모든 칸에 결과가 남아 있으면) 같은 답으로 시도를 또 만들지 않는다.
    if (order.length && order.every((id) => results[id])) {
      status = `${summary} 바뀐 칸이 없어 다시 채점하지 않았어요.`;
      return;
    }
    submitting = true;
    try {
      const answers = Object.fromEntries(order.map((id) => [id, values[id] ?? ""]));
      const res = await server.submitAttempt(answers);
      results = res.results;
      const count = { correct: 0, near: 0, wrong: 0 };
      for (const v of Object.values(res.results)) count[v] += 1;
      const near = count.near ? `, 거의 맞음 ${count.near}칸` : "";
      summary = `${order.length}칸 중 ${count.correct}칸 정답(${Math.round(res.score * 100)}점)${near}.`;
      status = `${summary} 고친 칸은 표시가 지워지고, 다시 확인할 수 있어요.`;
    } finally {
      submitting = false;
    }
  }
</script>

<div class:debug>
  <header class="bar">
    <h1>입력칸 겹치기 시험판 <small>Svelte</small></h1>
    <div class="actions">
      <label><input type="checkbox" bind:checked={debug} /> 빈칸 테두리 보기</label>
      <button type="button" id="check" disabled={submitting} onclick={submit}>정답 확인</button>
    </div>
  </header>
  <main id="pages" aria-live="polite">
    {#each pages as page (page.page_no)}
      <Page
        {page}
        bind:values
        {results}
        {onkeydown}
        onfocus={(blank) => (hint = blank.hint ? `힌트: ${blank.hint}` : "")}
        oninput={(id) => delete results[id]}
      />
    {/each}
  </main>
  <footer class="hint">
    {#if status}<p role="status">{status}</p>{/if}
    {#if hint}<p role="status">{hint}</p>{/if}
  </footer>
</div>
