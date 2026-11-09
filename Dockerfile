# Web 画面（STEP 10〜11）だけを動かすイメージ。音声づくり（OCR・TTS・FFmpeg）は含めない。
# 書籍データはイメージに入れず、実行時に PDF2AUDIOBOOK_DATA_DIR（Cloud Run では Cloud Storage を /data にマウント）から読む。
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PDF2AUDIOBOOK_DATA_DIR=/data
WORKDIR /srv
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn>=0.29" "jinja2>=3.1" "pyyaml>=6.0"
COPY app ./app
COPY config.yaml ./
CMD ["sh", "-c", "exec uvicorn app.web.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
