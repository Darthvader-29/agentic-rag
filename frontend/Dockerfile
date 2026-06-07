# frontend/Dockerfile

# 1) Build stage with Node 22
FROM node:22.11.0-alpine AS builder

WORKDIR /app

# Install dependencies
COPY package.json package-lock.json* ./
RUN npm install

# Copy all frontend source and build
COPY . .
RUN npm run build

# 2) Runtime stage (smaller image, still Node 22)
FROM node:22.11.0-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000

# Install only production dependencies
COPY package.json package-lock.json* ./
RUN npm install --omit=dev

# Copy built app from builder
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.ts ./next.config.ts
COPY --from=builder /app/app ./app
COPY --from=builder /app/components ./components
COPY --from=builder /app/services ./services
COPY --from=builder /app/types ./types
COPY --from=builder /app/lib ./lib

EXPOSE 3000

CMD ["npm", "run", "start"]
