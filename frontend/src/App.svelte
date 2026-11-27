<script>
  // 화면 이동. 주소의 # 뒤만 본다: #/ 처음 화면, #/sets/{세트 ID} 세트 화면.
  // # 뒤는 서버로 가지 않아서, 서버가 화면 주소마다 index.html을 돌려주는 설정 없이도 새로고침이 된다.
  import { onMount } from "svelte";
  import { LOGGED_IN, LOGIN_NEEDED } from "./api.js";
  import Home from "./Home.svelte";
  import Login from "./Login.svelte";
  import SetStatus from "./SetStatus.svelte";

  let hash = $state(location.hash);
  const setId = $derived(hash.match(/^#\/sets\/([^/]+)$/)?.[1] ?? null);

  // 어느 요청이든 401 unauthorized를 받으면 로그인 창을 띄운다(api.js). 화면은 지우지 않고 숨기기만 해서,
  // 답을 쓰다가 로그인이 풀려도 로그인 뒤 입력한 답이 그대로 남는다.
  let needLogin = $state(false);
  onMount(() => {
    const open = () => (needLogin = true);
    const close = () => (needLogin = false);
    window.addEventListener(LOGIN_NEEDED, open);
    window.addEventListener(LOGGED_IN, close);
    return () => {
      window.removeEventListener(LOGIN_NEEDED, open);
      window.removeEventListener(LOGGED_IN, close);
    };
  });
</script>

<svelte:window onhashchange={() => (hash = location.hash)} />

{#if needLogin}<Login />{/if}
<div inert={needLogin}>
  {#if setId}
    <!-- 세트가 바뀌면 화면을 새로 만들어 이전 세트의 폴링을 멈춘다. -->
    {#key setId}<SetStatus {setId} />{/key}
  {:else}
    <Home />
  {/if}
</div>

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
    --correct: #15803d;
    --near: #b45309;
    font-family: system-ui, "Malgun Gothic", sans-serif;
    color: var(--text);
  }
  :global(body) {
    margin: 0;
    background: var(--bg);
  }
</style>
