FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[test]"
COPY configs ./configs
COPY tests ./tests

# Results are written here; mount a host folder over it to keep them.
VOLUME ["/app/runs"]

ENTRYPOINT ["swarm-threat"]
CMD ["pilot", "--config", "configs/pilot.yaml"]
