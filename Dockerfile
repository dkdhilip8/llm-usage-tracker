# Production image: build the React SPA, then serve it + the API from one FastAPI process.
# Used by Render (runtime: docker). Local dev uses docker-compose instead.

# ---- stage 1: build the frontend ----
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build            # -> /fe/dist

# ---- stage 2: python runtime ----
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY --from=frontend /fe/dist ./app/static

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
