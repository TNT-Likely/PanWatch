# PanWatch Dockerfile
# 多阶段构建，减小最终镜像大小

# ===== Stage 1: 前端静态文件 =====
# 前端已在本机构建(frontend/dist/),直接复制到运行阶段
# 如需在 Docker 内重建,可回退至:使用 node:20-alpine 执行 pnpm install && pnpm build


# ===== Stage 2: Python 运行环境 =====
FROM python:3.11-slim

# 版本号（构建时传入）
ARG VERSION=dev

WORKDIR /app

# 安装系统依赖
# - tzdata: 时区数据（zoneinfo 模块需要）
# - 中文字体（K线截图需要）
# - Playwright Chromium 依赖的系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    # git: requirements.txt 中含 git+https 直链(tradingagents)
    git \
    # 中文字体
    fonts-noto-cjk \
    # Playwright Chromium 依赖
    # (这些库缺失会导致 playwright 提示 Host system is missing dependencies)
    libxcursor1 \
    libgtk-3-0 \
    libpangocairo-1.0-0 \
    libcairo-gobject2 \
    libgdk-pixbuf-2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    # 常见的 Chromium 运行时依赖（不同版本/发行版可能会缺）
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    libxi6 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    libxshmfence1 \
    libegl1 \
    libfontconfig1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# 复制依赖文件
COPY requirements.txt ./

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 注意: Playwright 浏览器将在首次启动时自动安装到 data 目录
# 这样可以减小镜像体积，并支持跨版本持久化

# 复制后端代码
COPY src/ ./src/
COPY server.py ./
COPY prompts/ ./prompts/

# 写入版本号
RUN echo "${VERSION}" > VERSION

# 复制本地已构建的前端静态文件
COPY frontend/dist ./static/

# 创建数据目录
RUN mkdir -p /app/data

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data
ENV DOCKER=1

# 默认时区（可在 docker run 时用 -e TZ=... 覆盖）
ENV TZ=Asia/Shanghai

# 暴露端口（保持 8000 不变，避免影响存量用户升级）
EXPOSE 8000

# 健康检查（使用 Python）
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# 启动命令
CMD ["python", "server.py"]
