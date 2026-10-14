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
COPY pyproject.toml ./
COPY src/ ./src/
# score_results.py runs inside the CodeBuild sandbox, but bmem.evaluate has to
# read it from disk to upload it. In a source checkout it is found at
# <repo>/infra/; once pip-installed there is no repo, so it ships inside the
# package instead -- see _find_score_results in evaluate.py.
COPY infra/score_results.py ./src/bmem/score_results.py
RUN pip install --no-cache-dir .

# The built frontend lands where api.py already expects and serves static
# assets from -- overwrites the placeholder .gitkeep, nothing else needed on
# the FastAPI side.
COPY --from=web-build /web/dist/ ./src/bmem/static/

EXPOSE 8000

# Not --reload: that watches the filesystem for changes, which is a
# development-time behavior with no purpose in a container that is rebuilt
# and redeployed on every change instead.
CMD ["uvicorn", "bmem.api:app", "--host", "0.0.0.0", "--port", "8000"]
