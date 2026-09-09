# Next.js web UI.
# Build context: repository root.
#
# The monorepo layout is reproduced inside the image (/repo/apps/web, not /app).
# `output: "standalone"` writes paths relative to outputFileTracingRoot, which
# next.config.ts resolves to the repo root; flattening apps/web to the image
# root would put server.js somewhere neither the config nor the CMD expects.

FROM node:22-alpine AS deps
WORKDIR /repo/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /repo
COPY packages/ ./packages/
COPY apps/web/ ./apps/web/
COPY --from=deps /repo/apps/web/node_modules ./apps/web/node_modules
WORKDIR /repo/apps/web

# NEXT_PUBLIC_* values are inlined at build time, so the API URL the browser
# uses has to be known here rather than at container start.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8080
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /repo
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# standalone traces exactly the files the server needs, so the runtime image
# carries neither the full node_modules tree nor the build cache. Static assets
# and public/ are not traced and must be copied alongside it.
COPY --from=build /repo/apps/web/.next/standalone ./
COPY --from=build /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=build /repo/apps/web/public ./apps/web/public

USER node
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
