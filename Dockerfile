# 운영 이미지(ADR-007). api·worker·migrate가 같은 이미지를 쓰고 명령만 다르다(deploy/compose.yml).
# 시연 기기가 x86_64라 linux/amd64로만 빌드한다(#83).

# --- 1단계: 웹 페이지 빌드. node는 최종 이미지에 남지 않는다 ---
FROM node:22-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 2단계: 실행 이미지 ---
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /usr/local/bin/uv

WORKDIR /app
# 바이트코드를 미리 만들어 기동을 빠르게 한다. Python은 이미지의 것을 쓰고 내려받지 않는다.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

# 의존성 층을 먼저 설치한다. 코드만 바뀌면 이 층은 캐시를 그대로 쓴다.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# README.md는 pyproject의 readme 필드 때문에 프로젝트 설치에 필요하다.
COPY README.md alembic.ini ./
COPY cloze ./cloze
RUN uv sync --locked --no-dev
COPY --from=frontend /src/frontend/dist ./frontend/dist

ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# root로 돌리지 않는다. 저장소 폴더는 미리 만들어 둔다: named volume은 첫 마운트 때
# 이 폴더의 소유자를 이어받아, app 사용자가 쓸 수 있다.
RUN useradd --system --uid 10001 app && mkdir -p /data/storage && chown app /data/storage
USER app

EXPOSE 8000
CMD ["uvicorn", "--factory", "cloze.api.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
