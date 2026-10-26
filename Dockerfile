FROM node:20-alpine AS frontend
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY web/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt requirements-cad.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-cad.txt
COPY . .
COPY --from=frontend /web/dist ./web/dist
EXPOSE 10000
CMD ["sh", "-c", "uvicorn cad.app:app --host 0.0.0.0 --port ${PORT:-10000}"]
