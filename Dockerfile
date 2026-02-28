FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
RUN uv pip install --system -e .

COPY . .

EXPOSE 8000

CMD ["python", "-m", "app.main"]
