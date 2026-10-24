# syntax=docker/dockerfile:1

FROM node:20-alpine AS deps
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci

# compose uses this: source is bind-mounted over /app, node_modules stays from the image
FROM deps AS dev
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]

# not used by compose yet -- kept so `docker build --target prod` produces a real build
FROM deps AS build
COPY . .
RUN npm run build

FROM build AS prod
ENV NODE_ENV=production
EXPOSE 3000
CMD ["npm", "run", "start"]
