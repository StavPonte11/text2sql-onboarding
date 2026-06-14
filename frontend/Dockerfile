# Build stage
FROM node:22-bookworm-slim AS builder

# Enable pnpm via corepack
RUN corepack enable && corepack prepare pnpm@latest --activate

WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm run build

# Serve stage
FROM nginxinc/nginx-unprivileged:alpine

# Set default service URLs, can be overridden via OpenShift environment variables
ENV BACKEND_URL=http://localhost:8000
ENV AGENT_URL=http://localhost:8001

# Copy the config as a template to allow Nginx to substitute the ${BACKEND_URL} variable at startup
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
