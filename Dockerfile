# 电子礼簿 NAS 持久化版 —— 自包含镜像
#
# 用途：把前端 + 后端一起打进镜像，部署时无需挂载源码目录。
# 适合「推送到自己的镜像仓库再分发到多台机器」的场景。
#
# 构建：
#   docker build -t gift-book-nas:latest .
# 运行：
#   docker run -d --name gift-book -p 8094:3000 \
#     -v /你的数据目录:/data --restart unless-stopped gift-book-nas:latest
#
# 注意：后端只用 Python 标准库，没有 pip 依赖，所以镜像里不需要装任何包。

FROM python:3-alpine

WORKDIR /app

# 后端服务（零第三方依赖）
COPY server.py /app/server.py

# 前端网页（原版 + 数据层改造），容器内统一放 /home/static
COPY src /home/static

ENV GB_DATA_DIR=/data \
    GB_STATIC_DIR=/home/static \
    GB_PORT=3000 \
    TZ=Asia/Shanghai

# 数据目录声明为卷，避免忘记挂载时数据写进容器层
VOLUME ["/data"]

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:3000/api/health',timeout=3)"

CMD ["python3", "/app/server.py"]
