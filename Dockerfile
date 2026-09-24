FROM python:3.12-slim AS production

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY dbt ./dbt
RUN pip install --no-cache-dir .

ENV DATABASE_PATH=/app/data/earnings.duckdb
VOLUME ["/app/data"]
ENTRYPOINT ["earnings-notifier"]
CMD ["run"]

FROM production AS test
COPY tests ./tests
RUN pip install --no-cache-dir '.[dev]'
ENTRYPOINT []
CMD ["sh", "-c", "pytest -q && ruff check ."]

