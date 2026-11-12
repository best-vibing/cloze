<script>
  // 앱 골격: 세트 하나를 2초마다 조회해 상태와 진행률을 보여 준다. 입력칸 겹치기는 풀이 화면 PR(#58)에서 붙인다.
  // 서버와는 api.js로만 주고받는다. fetch를 여기서 부르지 않는다.
  import { onMount } from "svelte";
  import { ApiError, demoSetId, getBlankSet } from "./api.js";

  const POLL_MS = 2000;
  const LABEL = {
    queued: "순서를 기다리는 중",
    running: "빈칸을 만드는 중",
    done: "완료",
    partial: "일부 쪽만 완료",
    failed: "만들지 못했어요",
  };

  const setId = new URLSearchParams(location.search).get("set") ?? demoSetId;
  let blankSet = $state(null);
  let problem = $state("");

  const finished = $derived(blankSet && !["queued", "running"].includes(blankSet.status));
  const blankCount = $derived(blankSet?.pages.reduce((n, p) => n + p.blanks.length, 0) ?? 0);

  onMount(() => {
    let timer;
    async function poll() {
      try {
        blankSet = await getBlankSet(setId);
        problem = "";
        if (!["queued", "running"].includes(blankSet.status)) return;
      } catch (e) {
        // 서버 문장(message)은 텍스트로만 넣는다. Svelte {…}는 HTML로 해석하지 않는다.
        problem = e instanceof ApiError ? e.message : "연결에 문제가 있어요. 잠시 후 다시 시도해 주세요.";
        if (e instanceof ApiError && e.status < 500) return; // 401·404는 다시 물어봐도 같다
      }
      timer = setTimeout(poll, POLL_MS);
    }
    if (setId) poll();
    else problem = "열 빈칸 세트가 없어요. 주소에 ?set=세트ID 를 붙여 주세요.";
    return () => clearTimeout(timer);
  });
</script>

<main>
  <h1>Cloze</h1>
  {#if problem}
    <p class="problem" role="alert">{problem}</p>
  {/if}
  {#if blankSet}
    <section aria-live="polite">
      <p><strong>{LABEL[blankSet.status] ?? blankSet.status}</strong></p>
      <progress max={blankSet.progress.total} value={blankSet.progress.done}></progress>
      <span>{blankSet.progress.done} / {blankSet.progress.total}쪽</span>
      {#if blankSet.preset_reason}<p>{blankSet.preset_reason}</p>{/if}
      {#if blankSet.error}<p class="problem">{blankSet.error.message}</p>{/if}
      {#if finished && !blankSet.error}
        <p>{blankSet.pages.length}쪽, 빈칸 {blankCount}개를 받았어요.</p>
      {/if}
    </section>
  {:else if !problem}
    <p>불러오는 중…</p>
  {/if}
</main>

<style>
  main {
    max-width: 720px;
    margin: 0 auto;
    padding: 16px;
    font-family: system-ui, "Malgun Gothic", sans-serif;
  }
  progress {
    width: 240px;
    vertical-align: middle;
  }
  .problem {
    color: #b91c1c;
  }
</style>
