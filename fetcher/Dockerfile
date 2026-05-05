FROM python:3.11-slim

WORKDIR /srv

# Chromium runtime deps for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      fonts-liberation \
      libasound2 \
      libatk-bridge2.0-0 \
      libatk1.0-0 \
      libatspi2.0-0 \
      libcups2 \
      libdbus-1-3 \
      libdrm2 \
      libgbm1 \
      libglib2.0-0 \
      libgtk-3-0 \
      libnspr4 \
      libnss3 \
      libpango-1.0-0 \
      libwayland-client0 \
      libx11-6 \
      libx11-xcb1 \
      libxcb1 \
      libxcomposite1 \
      libxdamage1 \
      libxext6 \
      libxfixes3 \
      libxkbcommon0 \
      libxrandr2 \
      wget \
      xdg-utils \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir ".[headless]"

# Pre-install the Chromium binary (deps already installed above).
RUN python -m playwright install chromium

COPY app ./app

EXPOSE 8001
ENV WORKERS=1
CMD uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers ${WORKERS}
