FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 8800

# AGENT_SHARED_SECRET 必须由部署环境注入；为空时服务会 fail fast。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8800"]
