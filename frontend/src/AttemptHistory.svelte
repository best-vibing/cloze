<script>
  // 이 세트에서 낸 시도 목록(#36). 정답을 보기 전의 풀이(처음 풀이)와 본 뒤의 풀이(복습)를 나눠 보인다.
  // 어느 쪽인지는 서버가 정한 after_reveal을 그대로 쓴다(채점·입력칸 설계 2절). 응답 모양은 계약 2절.
  import { formatTime } from "./messages.js";

  let { attempts } = $props();

  // 낸 순서대로. 시각 문자열의 표기(시간대 등)가 달라도 순서가 맞게 숫자로 비교한다.
  const sorted = $derived(
    [...attempts].sort((a, b) => Date.parse(a.submitted_at) - Date.parse(b.submitted_at)),
  );
  const groups = $derived([
    { title: "처음 풀이", list: sorted.filter((a) => !a.after_reveal) },
    { title: "복습", list: sorted.filter((a) => a.after_reveal) },
  ]);
</script>

<details class="history">
  <summary>풀이 기록 (제출 {attempts.length}번)</summary>
  {#each groups as group (group.title)}
    <p class="group"><strong>{group.title}</strong></p>
    {#if group.list.length}
      <ol>
        {#each group.list as attempt (attempt.attempt_id)}
          <li>
            <span>{formatTime(attempt.submitted_at)}</span>
            <strong>{Math.round(attempt.score * 100)}점</strong>
            {#if attempt.revealed_at}<span class="muted">정답 봄</span>{/if}
          </li>
        {/each}
      </ol>
    {:else}
      <p class="muted">아직 없어요.</p>
    {/if}
  {/each}
</details>

<style>
  .history {
    margin: 8px 0;
  }
  summary {
    cursor: pointer;
  }
  p {
    margin: 0;
  }
  .group {
    margin: 12px 0 4px;
  }
  ol {
    margin: 0;
    padding-left: 24px;
  }
  li {
    padding: 2px 0;
  }
  li > * + * {
    margin-left: 12px;
  }
  .muted {
    color: var(--muted);
  }
</style>
