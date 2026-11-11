FROM python:3.12-slim

# libreoffice-impress: legacy .ppt conversion and PowerPoint slide thumbnails
# fonts-dejavu-core, fonts-liberation: fonts LibreOffice needs to render slides faithfully
# tesseract-ocr(-eng): OCR fallback for image-based slides
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-impress fonts-dejavu-core fonts-liberation \
        tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 app && chown -R app /srv
USER app

# Secrets (ANTHROPIC_API_KEY, TEN_APP_PASSWORD) are injected by Railway at runtime, never baked in.
EXPOSE 8501
CMD ["sh", "-c", "python -m streamlit run app.py --server.port ${PORT:-8501} --server.address 0.0.0.0 --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false"]
