FROM node:22.23.0-alpine3.23 AS build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx ng build --configuration production

FROM nginxinc/nginx-unprivileged:1.29.2-alpine3.22
COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /src/dist/frontend/browser /usr/share/nginx/html
ENV MF_CSP_CONNECT_EXTRA=""
EXPOSE 8080
