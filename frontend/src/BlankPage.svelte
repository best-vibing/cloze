<script>
  // 쪽 하나: 가린 이미지 위에 빈칸 입력칸을 겹친다. 규칙은 채점·입력칸 설계 3.2, 코드는 스파이크(#57) Page.svelte에서 옮겼다.
  // 서버가 준 글자(hint 등)는 Svelte {…}로만 넣는다. {…}는 HTML로 해석하지 않는다.
  // 제출(#36) 뒤에는 results(blank_id → correct·near·wrong)로 칸을 표시하고 고치지 못하게 한다.
  // showAnswers면 이미지를 다시 받지 않고 칸 자리에 정답 글자(answers)를 그린다(설계 3.2 정답 공개).
  let {
    page,
    values = $bindable(),
    results = null,
    answers = null,
    showAnswers = false,
    broken,
    onkeydown,
    onfocus,
    onimageload,
    onimageerror,
    onretry,
  } = $props();

  const MAX_GROW = 3; // 입력이 길어질 때 bbox 폭의 최대 몇 배까지 늘릴지
  const RESULT_LABEL = { correct: "맞음", near: "거의 맞음", wrong: "틀림" };
  const measure = document.createElement("canvas").getContext("2d");
  let widths = $state({}); // blank_id → 입력에 맞춰 늘어난 폭(cqw)

  // Tab은 DOM 순서로 움직이므로, 칸을 order(읽는 순서) 순서로 그린다.
  const blanks = $derived([...page.blanks].sort((a, b) => a.order - b.order));
  const revealed = $derived(showAnswers && answers !== null);

  /** 칸에 보일 글자. 정답을 보는 중이면 정답, 아니면 내 입력. */
  const shown = (id) => (revealed ? (answers[id] ?? "") : (values[id] ?? ""));

  /**
   * 칸 폭(cqw). 정답은 원래 그 자리(bbox)에 있던 글자라 bbox 폭에 맞는다.
   * 내 입력은 늘어난 폭을 쓰되, 다시 풀기로 입력이 비면 bbox 폭으로 돌아간다.
   */
  function widthOf(blank) {
    const base = (blank.bbox[2] - blank.bbox[0]) * 100;
    if (revealed || !values[blank.blank_id]) return base;
    return widths[blank.blank_id] ?? base;
  }

  function labelOf(blank) {
    const result = results?.[blank.blank_id];
    const parts = [`페이지 ${page.page_no}, 빈칸 ${blank.order}`];
    if (result) parts.push(RESULT_LABEL[result]);
    if (revealed) parts.push("정답 표시 중");
    return parts.join(", ");
  }

  /** 정답 길이를 드러내지 않도록 폭은 bbox 폭에서 시작하고, 입력 글자 폭에 맞춰서만 늘린다. */
  function grow(event, blank) {
    const input = event.currentTarget;
    const [x0, , x1] = blank.bbox;
    const minCqw = (x1 - x0) * 100;
    const maxCqw = Math.max(minCqw, Math.min(minCqw * MAX_GROW, (1 - x0) * 100));
    const css = getComputedStyle(input);
    measure.font = css.font;
    const padding = parseFloat(css.paddingLeft) + parseFloat(css.paddingRight) + 2;
    const pageWidth = input.parentElement.clientWidth;
    if (!pageWidth) return;
    const textCqw = ((measure.measureText(input.value).width + padding) / pageWidth) * 100;
    widths[blank.blank_id] = Math.min(Math.max(minCqw, textCqw), maxCqw);
  }
</script>

<section class="page" aria-label="페이지 {page.page_no}">
  <!-- 비율을 직접 주어 그림이 오기 전이나 실패해도 자리를 잡는다. 쪽이 많을 때 보이는 쪽만 받는다. -->
  <img
    src={page.image_url}
    alt="페이지 {page.page_no}(빈칸 자리를 가린 이미지)"
    width={page.width_pt}
    height={page.height_pt}
    style:aspect-ratio="{page.width_pt} / {page.height_pt}"
    loading="lazy"
    onload={() => onimageload(page.page_no)}
    onerror={() => onimageerror(page.page_no)}
  />
  {#if broken}
    <div class="broken" role="alert">
      <p>{page.page_no}쪽 그림을 불러오지 못했어요.</p>
      <button type="button" onclick={() => onretry(page.page_no)}>다시 불러오기</button>
    </div>
  {:else}
    <!-- 그림이 없는 쪽에서는 입력칸을 그리지 않는다. 입력한 값은 부모(values)에 남아 다시 불러오면 돌아온다. -->
    {#each blanks as blank (blank.blank_id)}
      {@const [x0, y0, , y1] = blank.bbox}
      <input
        id="blank-{blank.blank_id}"
        class="blank"
        type="text"
        autocomplete="off"
        autocapitalize="off"
        spellcheck="false"
        aria-label={labelOf(blank)}
        aria-invalid={results?.[blank.blank_id] === "wrong"}
        readonly={results !== null}
        data-result={results?.[blank.blank_id]}
        style:left="{x0 * 100}%"
        style:top="{y0 * 100}%"
        style:height="{(y1 - y0) * 100}%"
        style:width="{widthOf(blank)}cqw"
        style:--font-ratio={blank.style.font_size_ratio}
        style:--font-color={blank.style.font_color}
        style:--bg-color={blank.style.bg_color}
        bind:value={() => shown(blank.blank_id), (v) => (values[blank.blank_id] = v)}
        oninput={(e) => grow(e, blank)}
        onkeydown={(e) => onkeydown(e, blank.blank_id)}
        onfocus={() => onfocus(blank)}
      />
    {/each}
  {/if}
</section>

<style>
  /* 쪽 한 장. 입력칸 좌표와 글자 크기의 기준이 되는 컨테이너다. */
  .page {
    position: relative;
    container-type: inline-size;
    width: 100%;
    background: var(--surface);
    box-shadow: 0 1px 4px rgb(0 0 0 / 0.15);
  }
  img {
    display: block;
    width: 100%;
    height: auto;
  }
  /* left/top/height는 퍼센트, 폭과 글자 크기는 cqw라 창 크기가 바뀌어도 비율이 같다. */
  .blank {
    position: absolute;
    box-sizing: border-box;
    margin: 0;
    padding: 0 0.15em;
    border: 0;
    border-bottom: 1px dashed rgb(0 0 0 / 0.25);
    border-radius: 0;
    /* API에 글꼴 정보가 없어 강의자료에 흔한 Arial 계열을 먼저 쓴다. 한글은 맑은 고딕으로 넘어간다. */
    font-family: Arial, Helvetica, "Malgun Gothic", sans-serif;
    font-size: calc(var(--font-ratio) * 100cqw);
    line-height: 1;
    color: var(--font-color);
    background: var(--bg-color);
    outline: none;
  }
  /* 늘어난 칸이 이웃 글자를 가리는 것을 포커스일 때만 알린다(설계 3.2 넘침). */
  .blank:focus {
    z-index: 1;
    border-bottom-color: var(--accent);
    box-shadow: -3px 0 0 var(--accent), 0 2px 8px rgb(0 0 0 / 0.25);
  }
  /* 채점 결과. 색만으로 구분하지 않도록 밑줄 모양도 다르게 한다(실선·점선·이중선). */
  .blank[data-result] {
    border-bottom-width: 2px;
    cursor: default;
  }
  .blank[data-result="correct"] {
    border-bottom: 2px solid var(--correct);
  }
  .blank[data-result="near"] {
    border-bottom: 2px dashed var(--near);
  }
  .blank[data-result="wrong"] {
    border-bottom: 3px double var(--danger);
  }
  .broken {
    position: absolute;
    inset: 0;
    z-index: 2;
    display: grid;
    place-content: center;
    justify-items: center;
    gap: 8px;
    background: var(--surface);
    color: var(--danger);
    text-align: center;
  }
  .broken p {
    margin: 0;
  }
  .broken button {
    font: inherit;
  }
</style>
