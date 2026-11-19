FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS base
ARG TARGETARCH
ENV DEV=false PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Europe/Zurich
WORKDIR /app
COPY pyproject.toml ./
COPY config/runtime-constraints.txt ./config/runtime-constraints.txt
COPY src/ ./src/
RUN pip install --no-cache-dir -c config/runtime-constraints.txt .
RUN python -c "import tzdata,shutil; from pathlib import Path; shutil.copytree(Path(tzdata.__file__).parent/'zoneinfo','/usr/share/zoneinfo',dirs_exist_ok=True)"
COPY config/supercronic.json /tmp/supercronic.json
COPY config/install-cron.py /tmp/install-cron.py
RUN TARGETARCH=${TARGETARCH} python /tmp/install-cron.py && rm /tmp/install-cron.py /tmp/supercronic.json
COPY config/crontab ./config/crontab
RUN supercronic -test /app/config/crontab
USER 1000:1000
ENTRYPOINT ["python", "-m", "gaps_scraper"]
CMD ["api", "--host", "0.0.0.0"]

FROM base AS tests
USER root
COPY tests/ /app/tests/
RUN pip install --no-cache-dir -c config/runtime-constraints.txt -r /app/tests/requirements.txt
USER 1000:1000
RUN python -m unittest discover -s tests -v

FROM tests AS browser-deps
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/test-browsers
RUN pip install --no-cache-dir -r /app/tests/requirements-browser.txt
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
FROM browser-deps AS browser-tests
USER 1000:1000
RUN python tests/browser_check.py

# Production : repartir de base, sans les dépendances ni navigateurs des tests.
FROM base AS runtime
