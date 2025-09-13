FROM ghcr.io/astral-sh/uv:python3.12-alpine

# 复制当前目录所有内容到 /app
COPY . /app

# 设置工作目录
WORKDIR /app

# 执行 uv sync 命令
RUN uv sync

# 暴露 8080 端口
EXPOSE 8080

# 创建配置目录并添加挂载点
RUN mkdir -p /app/config
VOLUME ["/app/config"]

# 启动 uv 运行 main.py
CMD ["uv", "run", "main.py"]