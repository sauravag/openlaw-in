# OpenLaw.in frontend image (Task 7: one-command self-hosting).
#
# `OPENLAW_API_URL` (read by next.config.ts's `rewrites()`, NOT the
# `NEXT_PUBLIC_...` convention -- this value is only ever read on the
# server, never inlined into the browser bundle) must resolve to the `api`
# service's address AS SEEN FROM INSIDE THIS CONTAINER on the Compose
# network -- i.e. `http://api:8000` (the service name Docker Compose's
# built-in DNS resolves), never `localhost`, which inside this container
# means "this container", not the api one.
#
# It is set ONLY at build time (`ARG`, below), not at container runtime
# (Task 7 review, M5, verified): Next.js resolves `rewrites()` once, at
# `next build`, baking the destination into `.next/routes-manifest.json` --
# a runtime-only `docker-compose.yml` `environment:` override has NO effect
# on an already-built image. To point a built image at a different `api`
# address, rebuild with a different `--build-arg OPENLAW_API_URL=...`;
# there is no runtime knob for this.
#
# `node:26-slim`, not `node:22`: verified by actually building this image
# (Task 7 review round 2) -- `node:22-slim` ships npm 10.9.9, which fails
# `npm ci` on this lockfile with an ERESOLVE peer-dependency error
# (`vitest@5.0.1` wants `@types/node@^22||>=24`, the root project pins
# `^20`) that this repo's actual npm (11.19.1, bundled with Node 26) does
# not raise for the same lockfile. Pinning the image's Node major to match
# the major actually used to generate `package-lock.json` is what makes
# `npm ci` reproduce the same install here as on a real dev machine, not an
# arbitrary choice -- if `web/package.json` ever gains an `engines` field,
# match that here instead of this comment.
FROM node:26-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:26-slim AS builder
WORKDIR /app
ARG OPENLAW_API_URL=http://api:8000
ENV OPENLAW_API_URL=$OPENLAW_API_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# `web/public` is empty and untracked -- git does not version empty
# directories, so on a fresh clone it does not exist on disk at all (Task 7
# review, C1). Without this, the runner stage's later
# `COPY --from=builder /app/public` fails with "not found" and the ONE
# documented command (`docker compose up -d`) cannot even build the image.
RUN mkdir -p public
RUN npm run build

# Separate, production-only install: `deps` above (used for `npm run build`)
# needs devDependencies (TypeScript, Tailwind, the Vitest toolchain); the
# runner below does not, and shipping them anyway needlessly bloats the
# runtime image (Task 7 review, L4).
FROM node:26-slim AS prod-deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM node:26-slim AS runner
WORKDIR /app
ENV NODE_ENV=production

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next ./.next
COPY --from=prod-deps --chown=nextjs:nodejs /app/node_modules ./node_modules
COPY --from=builder --chown=nextjs:nodejs /app/package.json ./package.json

# Never serves a request as root (Task 7 review, M3).
USER nextjs

EXPOSE 3000
CMD ["npm", "run", "start"]
