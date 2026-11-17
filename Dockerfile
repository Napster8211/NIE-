FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PORT=8000

WORKDIR /app

# Install system utilities, certificates, and media codecs
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium and all OS libraries as root
RUN playwright install --with-deps chromium

# Copy application source code
COPY . .

# Install uvicorn wrapper to handle unexpanded $PORT arguments from cloud custom start commands
RUN cp uvicorn_wrapper.py /usr/local/bin/uvicorn && chmod +x /usr/local/bin/uvicorn

# Expose service port and start application
EXPOSE 8000
CMD ["python", "run.py"]
