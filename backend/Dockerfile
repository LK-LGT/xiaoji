FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# 数据卷：SQLite 库、导出产物、知识库文件都落在 /app/data
VOLUME ["/app/data"]

EXPOSE 8000

# 先迁移到 head，再启动；生产环境应把 DATABASE_URL / ALEMBIC_EXPECT_TARGET
# 指向 PostgreSQL。
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
