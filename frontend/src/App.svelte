<script>
  // 화면 이동. 주소의 # 뒤만 본다: #/ 처음 화면, #/sets/{세트 ID} 세트 화면.
  // # 뒤는 서버로 가지 않아서, 서버가 화면 주소마다 index.html을 돌려주는 설정 없이도 새로고침이 된다.
  import Home from "./Home.svelte";
  import SetStatus from "./SetStatus.svelte";

  let hash = $state(location.hash);
  const setId = $derived(hash.match(/^#\/sets\/([^/]+)$/)?.[1] ?? null);
</script>

<svelte:window onhashchange={() => (hash = location.hash)} />

{#if setId}
  <!-- 세트가 바뀌면 화면을 새로 만들어 이전 세트의 폴링을 멈춘다. -->
  {#key setId}<SetStatus {setId} />{/key}
{:else}
  <Home />
{/if}

<style>
  :global(:root) {
    --bg: #eef0fb;
    --surface: #ffffff;
    --text: #1f2330;
    --muted: #5f6475;
    --line: #c9cde0;
    --accent: #4f46e5;
    --accent-weak: #e7e6fd;
    --danger: #b91c1c;
    font-family: system-ui, "Malgun Gothic", sans-serif;
    color: var(--text);
  }
  :global(body) {
    margin: 0;
    background: var(--bg);
  }
</style>
