# One image for the whole workbench: the React frontend is built, then
# served by the FastAPI backend (FRONTEND_DIST_DIR), so a deployment is a
# single web service plus a database. Works on any host that runs a
# Dockerfile (Render, Railway, Fly.io, Azure App Service, AWS, ...).
#
#   docker build -t risk-workbench .
#   docker run -p 8000:8000 --env-file backend/.env risk-workbench

# --- 1. Build the frontend ---------------------------------------------------
FROM node:24-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Empty = same origin: the browser calls /api on whatever URL served the page.
ENV VITE_API_BASE_URL=""
RUN npm run build

# --- 2. Backend runtime ------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIST_DIR=/app/frontend/dist

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/ ./
COPY --from=frontend /src/frontend/dist /app/frontend/dist

# Uploaded evidence files and backups are written under /app/backend
# (uploaded_files/, backups/). Mount a persistent disk there in production
# or they are lost when the container is replaced.
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
# PORT is set by most hosting platforms; 8000 otherwise. --proxy-headers
# lets the app see the original https:// scheme behind the host's proxy.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
