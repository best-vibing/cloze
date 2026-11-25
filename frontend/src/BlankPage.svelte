<script>
  // 쪽 하나: 가린 이미지 위에 빈칸 입력칸을 겹친다. 규칙은 채점·입력칸 설계 3.2, 코드는 스파이크(#57) Page.svelte에서 옮겼다.
  // 서버가 준 글자(hint 등)는 Svelte {…}로만 넣는다. {…}는 HTML로 해석하지 않는다.
  let { page, values = $bindable(), broken, onkeydown, onfocus, onimageload, onimageerror, onretry } = $props();

  const MAX_GROW = 3; // 입력이 길어질 때 bbox 폭의 최대 몇 배까지 늘릴지
  const measure = document.createElement("canvas").getContext("2d");
  let widths = $state({}); // blank_id → 입력에 맞춰 늘어난 폭(cqw)

  // Tab은 DOM 순서로 움직이므로, 칸을 order(읽는 순서) 순서로 그린다.
  const blanks = $derived([...page.blanks].sort((a, b) => a.order - b.order));

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
      {@const [x0, y0, x1, y1] = blank.bbox}
      <input
        id="blank-{blank.blank_id}"
        class="blank"
        type="text"
        autocomplete="off"
        autocapitalize="off"
        spellcheck="false"
        aria-label="페이지 {page.page_no}, 빈칸 {blank.order}"
        style:left="{x0 * 100}%"
        style:top="{y0 * 100}%"
        style:height="{(y1 - y0) * 100}%"
        style:width="{widths[blank.blank_id] ?? (x1 - x0) * 100}cqw"
        style:--font-ratio={blank.style.font_size_ratio}
        style:--font-color={blank.style.font_color}
        style:--bg-color={blank.style.bg_color}
        bind:value={values[blank.blank_id]}
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
