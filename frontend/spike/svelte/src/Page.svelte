<script>
  // 쪽 하나: 가린 이미지 위에 빈칸 입력칸을 겹친다. 좌표·글자 크기 규칙은 vanilla 후보와 같다.
  let { page, values = $bindable(), results, onkeydown, onfocus, oninput } = $props();

  const MAX_GROW = 3; // 입력이 길어질 때 bbox 폭의 최대 몇 배까지 늘릴지
  const measure = document.createElement("canvas").getContext("2d");
  let widths = $state({}); // blank_id → 입력에 맞춰 늘어난 폭(cqw)

  function grow(event, blank) {
    const input = event.currentTarget;
    const [x0, , x1] = blank.bbox;
    const minCqw = (x1 - x0) * 100;
    const maxCqw = Math.min(minCqw * MAX_GROW, (1 - x0) * 100);
    const css = getComputedStyle(input);
    measure.font = css.font;
    const padding = parseFloat(css.paddingLeft) + parseFloat(css.paddingRight) + 2;
    const pageWidth = input.parentElement.clientWidth;
    const textCqw = ((measure.measureText(input.value).width + padding) / pageWidth) * 100;
    widths[blank.blank_id] = Math.min(Math.max(minCqw, textCqw), maxCqw);
    oninput(blank.blank_id);
  }
</script>

<section class="page" aria-label="페이지 {page.page_no}">
  <img
    src={page.image_url}
    alt="페이지 {page.page_no}(빈칸 자리를 가린 이미지)"
    width={page.width_pt}
    height={page.height_pt}
  />
  {#each page.blanks as blank (blank.blank_id)}
    {@const [x0, y0, x1, y1] = blank.bbox}
    <!-- 정답 길이가 아니라 bbox 폭에서 시작한다. 글자는 Svelte가 텍스트로만 넣는다(HTML 해석 없음). -->
    <input
      id="blank-{blank.blank_id}"
      class="blank"
      type="text"
      autocomplete="off"
      spellcheck="false"
      aria-label="페이지 {page.page_no}, 빈칸 {blank.order}"
      aria-invalid={results[blank.blank_id] ? results[blank.blank_id] !== "correct" : undefined}
      data-result={results[blank.blank_id]}
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
</section>
