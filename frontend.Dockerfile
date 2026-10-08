# Builds the UI in frontend/, then serves it with nginx, which forwards the API to the backend.
# Built from the repo root; frontend.Dockerfile.dockerignore keeps the context to what it needs.
FROM node:22-alpine AS build

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM nginx:1.29-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
