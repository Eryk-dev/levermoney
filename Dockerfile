## Stage 1: Build dashboard
FROM node:20-alpine AS dashboard-build
WORKDIR /dashboard
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ .
RUN npm run build

## Stage 2: Python API + static dashboard
FROM python:3.12-slim
WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=dashboard-build /dashboard/dist /code/dashboard-dist

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
