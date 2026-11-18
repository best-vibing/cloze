import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [svelte()],
  // 빌드 결과(dist/)를 어느 주소 아래에 두어도 열리도록 상대 경로로 만든다(FastAPI 정적 제공 확인용).
  base: "./",
  // 순수 HTML/JS 후보와 같은 가짜 데이터를 쓴다. 개발 서버와 빌드 결과에 그대로 복사된다.
  publicDir: "../fixtures",
  // 스타일은 vanilla 후보의 style.css를 같이 쓴다. 개발 서버가 frontend/spike 안의 파일만 읽게 허용한다.
  server: { fs: { allow: [".."] } },
});
