# syntax=docker/dockerfile:1
#
# Dashboard Next.js (ticket-ai-dashboard). Multi-stage: deps -> build -> runtime
# mínimo usando `output: "standalone"` (ver next.config.ts) para no copiar el
# node_modules completo a la imagen final.

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Solo NEXT_PUBLIC_* se hornea en el bundle del cliente durante el build; no
# hay ningún secreto server-only en este proyecto (ver .env.example). Se pasan
# como build args con defaults inofensivos -- en runtime real, Compose los
# vuelve a inyectar como environment: de todos modos.
ARG NEXT_PUBLIC_POCKETBASE_URL=http://localhost:8090
ARG NEXT_PUBLIC_INVOICE_API_BAS_URL=http://localhost:8000
ARG NEXT_PUBLIC_TEAM_SHEET_URL=
ARG NEXT_PUBLIC_TEAM_DRIVE_FOLDER_URL=
ENV NEXT_PUBLIC_POCKETBASE_URL=${NEXT_PUBLIC_POCKETBASE_URL}
ENV NEXT_PUBLIC_INVOICE_API_BAS_URL=${NEXT_PUBLIC_INVOICE_API_BAS_URL}
ENV NEXT_PUBLIC_TEAM_SHEET_URL=${NEXT_PUBLIC_TEAM_SHEET_URL}
ENV NEXT_PUBLIC_TEAM_DRIVE_FOLDER_URL=${NEXT_PUBLIC_TEAM_DRIVE_FOLDER_URL}
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

CMD ["node", "server.js"]
