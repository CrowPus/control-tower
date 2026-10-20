# Nia's browser (docs/pivot/NIA_BROWSER.md): one headless Chromium that Nia — and the owner, taking over — drives
# through a small internal API. Its own network (only the worker and the admin reach it), no database, no secrets
# but the derived token; private and internal addresses are refused inside the browser too.
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy
RUN pip install --no-cache-dir "playwright==1.49.0" "fastapi==0.115.6" "uvicorn==0.34.0" && useradd -m -u 10001 nia && mkdir -p /data && chown nia /data
COPY server.py /srv/server.py
USER nia
WORKDIR /srv
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=3)"
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
