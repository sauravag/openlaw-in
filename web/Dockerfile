# OpenLaw.in frontend image (Task 7: one-command self-hosting).
#
# `OPENLAW_API_URL` (read by next.config.ts's `rewrites()`, NOT the
# `NEXT_PUBLIC_...` convention -- this value is only ever read on the
# server, never inlined into the browser bundle) must resolve to the `api`
# service's address AS SEEN FROM INSIDE THIS CONTAINER on the Compose
# network -- i.e. `http://api:8000` (the service name Docker Compose's
# built-in DNS resolves), never `localhost`, which inside this container
# means "this container", not the api one. It is set at BOTH build time
# (baked into the compiled `.next` output, in case Next.js resolves
# `rewrites()` once at `next build`) and again at container start via
# docker-compose.yml's `environment:` (in case it instead re-reads
# `process.env` when `next start` boots) -- belt and braces, since which of
# the two actually applies is a Next.js internal this Dockerfile does not
# depend on either way.
FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS builder
WORKDIR /app
ARG OPENLAW_API_URL=http://api:8000
ENV OPENLAW_API_URL=$OPENLAW_API_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV OPENLAW_API_URL=http://api:8000
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json

EXPOSE 3000
CMD ["npm", "run", "start"]
