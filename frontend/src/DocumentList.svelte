<script>
  // 내 자료 목록(#35): 문서마다 만든 세트와 그 상태, 문서 지우기. 응답 모양은 계약 2절 GET /documents.
  // 처음 화면에 올 때(세트 화면에서 돌아올 때 포함)와 로그인한 뒤에 읽고, 만드는 중인 세트가 있으면 주기적으로 다시 읽는다.
  import { onMount, tick } from "svelte";
  import { ApiError, LOGGED_IN, deleteDocument, listDocuments } from "./api.js";
  import { STATUS_LABEL, actionErrorMessage, formatTime } from "./messages.js";

  // 목록은 문서 전체를 다시 받으므로 세트 화면(2초)보다 드물게 묻는다. [가설] 실측(#61) 뒤 조정한다.
  const POLL_MS = 5000;

  let documents = $state(null); // null이면 아직 받지 못했다
  let problem = $state("");
  let confirming = $state(null); // 지울지 묻는 중인 document_id
  let deleting = $state(null); // 지우는 중인 document_id

  const time = (iso) => Date.parse(iso) || 0;
  const newest = (sets) => Math.max(0, ...sets.map((s) => time(s.created_at)));

  // 최근에 세트를 만든 문서가 위로, 문서 안에서는 최근 세트가 위로 온다.
  const sorted = $derived(
    (documents ?? [])
      .map((doc) => ({
        ...doc,
        blank_sets: [...doc.blank_sets].sort((a, b) => time(b.created_at) - time(a.created_at)),
      }))
      .sort((a, b) => newest(b.blank_sets) - newest(a.blank_sets)),
  );
  const working = $derived(
    (documents ?? []).some((d) => d.blank_sets.some((s) => ["queued", "running"].includes(s.status))),
  );

  let timer;
  let latest = 0; // 겹친 요청은 마지막에 보낸 것의 응답만 쓴다(지운 문서가 늦게 온 옛 응답으로 되살아나지 않게)

  async function load() {
    clearTimeout(timer);
    const mine = ++latest;
    try {
      const list = await listDocuments();
      if (mine !== latest) return;
      documents = list;
      problem = "";
      if (working) timer = setTimeout(load, POLL_MS);
    } catch (e) {
      if (mine !== latest) return;
      // 401이면 App이 로그인 창을 띄우고, 로그인한 뒤 LOGGED_IN에서 다시 읽는다.
      if (e instanceof ApiError && e.code === "unauthorized") return;
      problem = e instanceof ApiError ? e.message : "목록을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.";
      // 만드는 중인 세트를 지켜보던 중이면 일시적인 실패일 수 있어 계속 묻는다. 성공하면 문구가 지워진다.
      if (working) timer = setTimeout(load, POLL_MS);
    }
  }

  onMount(() => {
    load();
    window.addEventListener(LOGGED_IN, load);
    return () => {
      latest += 1; // 화면을 떠난 뒤 온 응답은 버린다
      clearTimeout(timer);
      window.removeEventListener(LOGGED_IN, load);
    };
  });

  /** 지울지 묻기. 지우기 버튼이 사라지므로 포커스를 "취소"로 옮긴다(Enter를 눌러도 지워지지 않게). */
  async function ask(documentId) {
    confirming = documentId;
    await tick();
    document.getElementById(`cancel-${documentId}`)?.focus();
  }

  async function cancel(documentId) {
    confirming = null;
    await tick();
    document.getElementById(`delete-${documentId}`)?.focus();
  }

  /** 지우면 세트·빈칸·풀이 기록이 같이 사라지고 되돌릴 수 없다(계약 2절 DELETE). 그래서 ask()로 한 번 묻는다. */
  async function remove(documentId) {
    if (deleting) return;
    deleting = documentId;
    problem = "";
    try {
      await deleteDocument(documentId);
      forget(documentId);
    } catch (e) {
      // 이미 지워진 문서(다른 탭에서 지움 등)는 목록에서도 빼면 된다.
      if (e instanceof ApiError && e.code === "not_found") forget(documentId);
      else problem = actionErrorMessage(e);
    } finally {
      deleting = null;
    }
  }

  function forget(documentId) {
    latest += 1; // 지우기 전에 보낸 목록 요청의 응답이 문서를 되살리지 않게 한다
    documents = documents.filter((d) => d.document_id !== documentId);
    confirming = null;
  }
</script>

<section class="card" aria-labelledby="documents-title">
  <h2 id="documents-title">내 자료</h2>
  <!-- 실시간 영역은 글자가 바뀌기 전부터 DOM에 있어야 낭독된다. 비면 :empty로 숨긴다. -->
  <p class="problem" role="alert">{problem}</p>
  {#if documents === null}
    {#if !problem}<p class="muted">불러오는 중…</p>{/if}
  {:else if sorted.length === 0}
    <p class="muted">아직 올린 자료가 없어요.</p>
  {:else}
    <ul class="documents">
      {#each sorted as doc (doc.document_id)}
        <li>
          <div class="head">
            <strong class="name">{doc.filename}</strong>
            {#if confirming === doc.document_id}
              <span class="confirm">
                <span>지우면 이 자료의 빈칸과 풀이 기록도 사라지고 되돌릴 수 없어요.</span>
                <button
                  type="button"
                  class="danger"
                  disabled={deleting !== null}
                  onclick={() => remove(doc.document_id)}
                >
                  {deleting === doc.document_id ? "지우는 중…" : "지우기"}
                </button>
                <button
                  type="button"
                  id="cancel-{doc.document_id}"
                  disabled={deleting !== null}
                  onclick={() => cancel(doc.document_id)}
                >
                  취소
                </button>
              </span>
            {:else}
              <button
                type="button"
                id="delete-{doc.document_id}"
                aria-label="{doc.filename} 지우기"
                onclick={() => ask(doc.document_id)}
              >
                지우기
              </button>
            {/if}
          </div>
          <ul class="sets">
            {#each doc.blank_sets as set (set.blank_set_id)}
              <li>
                <a href="#/sets/{set.blank_set_id}">{formatTime(set.created_at)}에 만든 빈칸</a>
                <span class="status" class:failed={set.status === "failed"}>
                  {STATUS_LABEL[set.status] ?? set.status}
                </span>
              </li>
            {/each}
          </ul>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .card {
    background: var(--surface);
    border-radius: 16px;
    padding: 16px 20px;
    margin-top: 24px;
    text-align: left;
  }
  h2 {
    font-size: 1.1rem;
    margin: 0 0 8px;
  }
  p {
    margin: 4px 0;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .documents > li {
    padding: 12px 0;
    border-top: 1px solid var(--line);
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .name {
    overflow-wrap: anywhere;
  }
  .confirm {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    font-size: 0.9rem;
  }
  .sets {
    margin-top: 6px;
  }
  .sets li {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 2px 0;
    font-size: 0.95rem;
  }
  .status {
    color: var(--muted);
  }
  .status.failed {
    color: var(--danger);
  }
  button {
    font: inherit;
    font-size: 0.9rem;
    padding: 4px 12px;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
  }
  .danger {
    border-color: var(--danger);
    color: var(--danger);
  }
  button:disabled {
    opacity: 0.6;
    cursor: wait;
  }
  .muted {
    color: var(--muted);
  }
  .problem {
    color: var(--danger);
  }
  .problem:empty {
    display: none;
  }
</style>
