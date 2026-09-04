# ---------- InfoData 后端镜像 ----------
# 构建：docker build -f backend/Dockerfile -t infodata-backend .
# 运行：由 docker-compose 编排（依赖 mysql 服务）

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8

WORKDIR /app

# 先装依赖（利用 Docker 层缓存）
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 拷贝后端源码
COPY backend/app ./app
COPY backend/scripts ./scripts

EXPOSE 8000

# uvicorn 单进程（采集任务在后台线程/子进程运行；如需多核可加 --workers，注意任务并发）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
