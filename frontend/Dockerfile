# frontend/Dockerfile

# 1) Build stage with Node 22
FROM node:22.11.0-alpine AS builder

WORKDIR /app

# Install dependencies from the lockfile (reproducible — `npm ci` fails if package.json
# and package-lock.json disagree, unlike `npm install` which silently mutates the lock).
COPY package.json package-lock.json* ./
RUN npm ci

# Copy all frontend source and build. A .dockerignore keeps node_modules, .next, .env*,
# tests/docs and .git out of the build context (no stale deps, no secrets in a layer).
COPY . .
RUN npm run build

# 2) Runtime stage (smaller image, still Node 22)
FROM node:22.11.0-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000

# Install only production dependencies from the lockfile.
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev

# Copy built app from builder
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.ts ./next.config.ts
COPY --from=builder /app/app ./app
COPY --from=builder /app/components ./components
COPY --from=builder /app/services ./services
COPY --from=builder /app/types ./types
COPY --from=builder /app/lib ./lib

# Run as the unprivileged `node` user that ships with the official image (uid 1000)
# instead of root — least privilege for the runtime container. Own the app dir so the
# Next.js server can write its cache.
RUN chown -R node:node /app
USER node

EXPOSE 3000

# Liveness probe against the Next.js server. busybox `wget` ships in the alpine base,
# so this needs no extra package; `-q --spider` makes it exit non-zero on a bad status.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -q --spider http://localhost:3000/ || exit 1

CMD ["npm", "run", "start"]
