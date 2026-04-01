FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성 (matplotlib 한글 폰트)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 디렉토리 생성 + non-root user
RUN mkdir -p data logs \
    && useradd --no-create-home --shell /bin/false appuser \
    && chown -R appuser:appuser /app/data /app/logs

USER appuser

CMD ["python", "-m", "src.main"]
