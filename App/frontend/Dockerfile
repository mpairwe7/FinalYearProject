# syntax=docker/dockerfile:1.7
# =============================================================================
# URA Chatbot Frontend - Multi-stage Docker Build
# Uses Bun for dependency install and build, Node.js slim for runtime
# =============================================================================

# Stage 1: Install dependencies
FROM oven/bun:1.3.12-slim AS deps
WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

# Stage 2: Build the Next.js application
FROM oven/bun:1.3.12-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_URL=http://localhost:8087
ARG INTERNAL_API_URL=http://127.0.0.1:8887
ARG NEXT_PUBLIC_OIDC_ISSUER=""
ARG NEXT_PUBLIC_OIDC_CLIENT_ID=""
ARG NEXT_PUBLIC_OIDC_AUDIENCE=""
ARG NEXT_PUBLIC_OIDC_SCOPE=""
# Must match a value registered at the provider (Auth0 "Allowed Logout URLs").
# Those are matched exactly: our HF Space is registered with /signin, the ngrok
# tunnel with the bare origin. Empty here means the origin.
ARG NEXT_PUBLIC_OIDC_POST_LOGOUT_PATH="/signin"
ARG NEXT_PUBLIC_DEV_SIGNIN=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV INTERNAL_API_URL=$INTERNAL_API_URL
ENV NEXT_PUBLIC_OIDC_ISSUER=$NEXT_PUBLIC_OIDC_ISSUER
ENV NEXT_PUBLIC_OIDC_CLIENT_ID=$NEXT_PUBLIC_OIDC_CLIENT_ID
ENV NEXT_PUBLIC_OIDC_AUDIENCE=$NEXT_PUBLIC_OIDC_AUDIENCE
ENV NEXT_PUBLIC_OIDC_SCOPE=$NEXT_PUBLIC_OIDC_SCOPE
ENV NEXT_PUBLIC_OIDC_POST_LOGOUT_PATH=$NEXT_PUBLIC_OIDC_POST_LOGOUT_PATH
ENV NEXT_PUBLIC_DEV_SIGNIN=$NEXT_PUBLIC_DEV_SIGNIN
ENV NEXT_TELEMETRY_DISABLED=1

RUN bun run build

# Stage 3: Production runtime (minimal Node.js image)
# Next.js 16 requires Node.js 20.9+ — pin to a specific 20 LTS patch to
# guarantee the floor at build time and prevent silent downgrades.
FROM node:26.8-slim AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Non-root user for security
RUN groupadd --gid 10001 appuser && \
    useradd --uid 10001 --gid appuser --create-home --shell /usr/sbin/nologin appuser

# Copy standalone output from builder
COPY --from=builder --chown=appuser:appuser /app/.next/standalone ./
COPY --from=builder --chown=appuser:appuser /app/.next/static ./.next/static
COPY --from=builder --chown=appuser:appuser /app/public ./public

USER appuser
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD node -e "const http=require('http');const r=http.get('http://127.0.0.1:3000/',{timeout:3000},s=>{process.exit(s.statusCode===200?0:1)});r.on('error',()=>process.exit(1))"

CMD ["node", "server.js"]
