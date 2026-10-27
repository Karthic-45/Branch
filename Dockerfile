# Single-container deploy target: Elastic Beanstalk's Docker platform builds
# this server-side (no local Docker, no ECR) -- see infra/deploy_beanstalk.sh
# for why EB rather than App Runner, which the original architecture assumed.

# Stage 1: build the React frontend (web/). Its own stage, not a step in the
# Python stage below, so the final image never carries node/npm/node_modules
# -- only the static files that come out of `npm run build`.
FROM node:22-slim AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

# Dependencies before source: this layer only invalidates when pyproject.toml
# changes, not on every code edit.
COPY pyproject.toml README.md ./
COPY src/ ./src/
# score_results.py runs inside the CodeBuild sandbox, but bmem.evaluate has to
# read it from disk to upload it. In a source checkout it is found at
# <repo>/infra/; once pip-installed there is no repo, so it ships inside the
# package instead -- see _find_score_results in evaluate.py.
# Copy the built frontend into static directory BEFORE pip install
COPY --from=web-build /web/dist/ ./src/bmem/static/

RUN pip install --no-cache-dir -e .

# Also copy into site-packages in case package is imported non-editable
RUN python -c "import bmem, shutil; shutil.copytree('/web/dist', bmem.__path__[0] + '/static', dirs_exist_ok=True)" || true

EXPOSE 8000

ENV PORT=8000
CMD ["sh", "-c", "uvicorn bmem.api:app --host 0.0.0.0 --port ${PORT:-8000}"]


