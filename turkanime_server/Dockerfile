# TurkAnime API Server Dockerfile
# Multi-stage değil — runtime imajı; chromium artık gerekli değil çünkü
# tranimaci/openani Python tarafında WAF/scraper çözüyor.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=34665

WORKDIR /app

# Sistem bağımlılıkları — curl_cffi'ın libcurl-impersonate için minimum + curl
# (healthcheck) + git & openssh-client (Faz 12 arşiv yayıncısı; `git push`
# ikiliyi doğrudan çağırır, SSH anahtarıyla itme seçeneği için ssh de gerekli).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        git \
        openssh-client \
        libssl3 \
        libffi8 \
    && rm -rf /var/lib/apt/lists/*

# Requirements'ı önce kopyala — layer cache için
COPY turkanime_server/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Tüm projeyi kopyala — turkanime_api modülü relative import'larıyla beraber gerekli
COPY turkanime_api /app/turkanime_api
COPY turkanime_server /app/turkanime_server

# Çalışma dizini server klasörüne çek
WORKDIR /app/turkanime_server

EXPOSE 34665

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

CMD ["python", "app.py"]
