import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [svelte()],
  // 빌드 결과(dist/)를 FastAPI가 어느 주소 아래에서 내보내도 열리도록 상대 경로로 만든다.
  base: "./",
});
