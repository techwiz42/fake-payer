# The fake payer line. Build:  docker build -t fake-payer .      Run and deploy: see the Deploying section of README.md
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY fake_payer ./fake_payer
RUN pip install .

# The call log and the stored spoken refusals live in /data (a volume), so they survive a restart. The program runs as a user with no login.
RUN useradd --system --no-create-home --uid 10001 fakepayer && mkdir /data && chown fakepayer /data
USER fakepayer
VOLUME /data
WORKDIR /data

# Inside the container the program listens on every interface; the host decides who can reach it (see the Deploying section of README.md).
ENV LISTEN_HOST=0.0.0.0 PORT=8080 CALL_LOG_DIR=/data/calls AUDIO_CACHE_DIR=/data/audio_cache
EXPOSE 8080

CMD ["python", "-m", "fake_payer"]
