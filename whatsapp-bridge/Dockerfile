FROM node:20-slim

RUN apt-get update && apt-get install -y \
    chromium \
    fonts-liberation \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    ca-certificates \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
ENV WA_BRIDGE_PORT=3001
ENV WA_AUTH_DATA_DIR=/data/wwebjs_auth
ENV ACTIVE_SESSIONS_FILE=/data/active_sessions.json

WORKDIR /app

COPY package*.json ./
RUN npm ci --omit=dev

COPY . .

EXPOSE 3001

CMD ["npm", "start"]
